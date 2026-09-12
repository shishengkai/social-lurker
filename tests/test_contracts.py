import copy
import json
import os
import subprocess
from datetime import UTC, datetime

import httpx
import pytest

from social_lurker.config import validate
from social_lurker.control import Journal
from social_lurker.errors import LurkerError
from social_lurker.proofread import apply_edits, segments, validate_edits
from social_lurker.providers.fal import Fal
from social_lurker.providers.tikhub import TikHub, dy_work, wx_work
from social_lurker.util import now, safe_path, time_range


def test_env_never_interpolates_or_changes_process(env, monkeypatch):
    instance, _, _ = env
    monkeypatch.setenv("FAL_KEY", "host-secret")
    instance.set_credentials({"FAL_KEY": "${HOST_VALUE}", "TIKHUB_API_KEY": "it's-local"})
    assert instance.credentials() == {"FAL_KEY": "${HOST_VALUE}", "TIKHUB_API_KEY": "it's-local"}
    assert os.environ["FAL_KEY"] == "host-secret"
    assert instance.path.joinpath(".env").stat().st_mode & 0o777 == 0o600


def test_long_external_ids_and_invalid_response():
    result = wx_work(
        {
            "id": 14991940714846493129,
            "username": "x",
            "createtime": 1787188201,
            "objectDesc": {"description": "标题"},
        }
    )
    assert result.id == "14991940714846493129"
    assert result.published_at == 1787188201
    with pytest.raises(LurkerError):
        wx_work({"message": "invalid params"})
    with pytest.raises(LurkerError):
        dy_work({"aweme_id": float(14991940714846493129)})


def test_wechat_zero_flag_with_cursor_is_unknown():
    def respond(request):
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "username": "u",
                    "videos": [{"id": 1, "createtime": 1}],
                    "last_buffer": "next",
                    "up_continue": 0,
                },
            },
        )

    adapter = TikHub("key", httpx.Client(transport=httpx.MockTransport(respond)))
    result = adapter.page({"platform": "wechat_channels", "platform_account_id": "u"})
    assert result.has_more is None and result.cursor == "next"


def test_fal_text_required_empty_is_valid_and_no_error_json():
    for payload, valid in [
        ({"text": ""}, True),
        ({"text": "完整"}, True),
        ({"error": "failed", "text": ""}, False),
        ({"chunks": []}, False),
    ]:
        fal = Fal(
            "key",
            httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))),
        )
        if valid:
            assert fal.fetch_result("id") == payload["text"]
        else:
            with pytest.raises(LurkerError):
                fal.fetch_result("id")


def test_fal_submit_unknown_no_automatic_http_retry():
    calls = []

    def fail(request):
        calls.append(request)
        assert request.headers["X-Fal-No-Retry"] == "1"
        raise httpx.ReadTimeout("secret-url-token")

    fal = Fal("key", httpx.Client(transport=httpx.MockTransport(fail)))
    with pytest.raises(LurkerError) as error:
        fal.submit("https://v3.fal.media/input.mp3")
    assert error.value.code == "ASR_SUBMIT_UNKNOWN"
    assert "secret" not in str(error.value)
    assert len(calls) == 1


def test_proofread_exact_coverage_unicode_and_guardrails():
    raw = ("英文 words 不丢。\n" * 1000) + "😀尾部"
    parts = segments(raw)
    assert "".join(raw[s["start"] : s["end"]] for s in parts) == raw
    assert all(s["end"] - s["start"] <= 6000 for s in parts)
    segment = {"start": 0, "end": len(raw)}
    edits = [{"start": 0, "end": 2, "old": "英文", "replacement": "英语"}]
    assert apply_edits(raw, validate_edits(raw, segment, edits)) == "英语" + raw[2:]
    for invalid in (
        [{"start": 0, "end": 2, "old": "英文", "replacement": ""}],
        [{"start": 0, "end": 0, "old": "", "replacement": "总结如下"}],
        [{"start": 0, "end": 2, "old": "错误", "replacement": "英语"}],
        [{"start": 0, "end": 50, "old": raw[:50], "replacement": "摘要"}],
    ):
        with pytest.raises(LurkerError):
            validate_edits(raw, segment, invalid)


def test_month_range_clamps_and_is_timezone_aware():
    end = datetime(2024, 3, 31, 0, 0, tzinfo=UTC).timestamp()
    start, frozen = time_range(end, 1, "months", "Asia/Shanghai")
    assert datetime.fromtimestamp(start, UTC) == datetime(2024, 2, 29, 0, 0, tzinfo=UTC)
    assert frozen == end


def test_control_journal_replay_expiry_and_no_credential_hash(env):
    instance, _, _ = env
    journal = Journal(instance)
    count = []
    request = {"request_id": "req", "created_at": now()}

    def operation(intent):
        count.append(1)
        return {"updated_fields": ["FAL_KEY"]}

    assert journal.perform(request, "credentials", {"fields": ["FAL_KEY"]}, operation) == {
        "updated_fields": ["FAL_KEY"]
    }
    journal.perform(request, "credentials", {"fields": ["FAL_KEY"]}, operation)
    assert len(count) == 1
    with pytest.raises(LurkerError):
        journal.perform(request, "stop", {}, operation)
    with pytest.raises(LurkerError):
        journal.perform({"request_id": "old", "created_at": now() - 15 * 86400}, "stop", {}, operation)


def test_paths_and_config_are_strict(env, tmp_path):
    instance, _, settings = env
    for relative in ("../x", "/etc/passwd"):
        with pytest.raises(LurkerError):
            safe_path(instance.path, relative)
    (instance.path / "escape").symlink_to(tmp_path)
    with pytest.raises(LurkerError):
        safe_path(instance.path, "escape/file")
    altered = copy.deepcopy(settings)
    altered["services"]["proofreading"]["provider"] = "external_llm"
    with pytest.raises(LurkerError):
        validate(altered, instance.id)


def test_cli_json_envelope_and_control_with_broken_settings(env):
    instance, store, settings = env
    from conftest import author

    store.add_account(author(), "add")
    instance.settings_path.write_text("{broken")
    command = [
        os.sys.executable,
        "tools/dev.py",
        "--root",
        str(instance.root),
        "--instance",
        instance.id,
        "accounts",
        "--request-stdin",
    ]
    result = subprocess.run(
        command,
        input=json.dumps(
            {
                "protocol_version": 1,
                "request_id": "stop",
                "created_at": now(),
                "payload": {"action": "stop", "account_id": 1},
            }
        ),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["result"]["state"] == "stopped"
    assert len(result.stdout.splitlines()) == 1


def test_wechat_verified_empty_tail_can_retain_opaque_cursor():
    def respond(request):
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "username": "u",
                    "videos": [],
                    "count": 0,
                    "last_buffer": "opaque-tail",
                    "up_continue": 0,
                },
            },
        )

    adapter = TikHub("key", httpx.Client(transport=httpx.MockTransport(respond)))
    result = adapter.page({"platform": "wechat_channels", "platform_account_id": "u"})
    assert result.has_more is False and result.cursor == "opaque-tail"


def test_punctuation_replacement_cannot_disguise_content_deletion_or_insertion():
    for raw, edit in [
        ("正文。", {"start": 0, "end": 2, "old": "正文", "replacement": "。"}),
        ("正文。", {"start": 2, "end": 3, "old": "。", "replacement": "总结"}),
    ]:
        with pytest.raises(LurkerError):
            validate_edits(raw, {"start": 0, "end": len(raw)}, [edit])


def test_tikhub_internal_error_is_not_success_and_uses_bounded_retry():
    def respond(request):
        return httpx.Response(200, json={"code": 200, "data": {"message": "upstream error", "debug_id": "x"}})

    adapter = TikHub("key", httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(LurkerError) as error:
        adapter.page({"platform": "wechat_channels", "platform_account_id": "u"})
    assert error.value.code == "UPSTREAM_TEMPORARY" and error.value.retryable
