import io
import json

import pytest
from test_transport_adapters import transport

from social_lurker.cli import main
from social_lurker.config import Config, FileSecrets
from social_lurker.errors import LurkerError
from social_lurker.transport import API_ROOT, ENDPOINT_INFO, USER_INFO, Response, Transport


def pricing(cost=0, uri="/api/v1" + USER_INFO):
    return Response(200, {"code": 200, "data": {"endpoint_uri": uri, "endpoint_cost": cost}})


def account(**overrides):
    user = {
        "is_active": True,
        "account_disabled": False,
        "email_verified": True,
        "email": "private@example.invalid",
        "balance": 99,
    }
    user.update(overrides)
    return Response(
        200,
        {
            "code": 200,
            "api_key_data": {"api_key_status": 1, "api_key_name": "private-name"},
            "user_data": user,
        },
    )


def test_zero_price_before_auth_and_sanitized_result(tmp_path):
    t, _, calls, stats = transport(tmp_path, [pricing(), account()], Config(rps=10))
    t.secrets.set_key("file-fixture")
    result = t.check_credentials()
    assert result == {"credential_check": "passed", "endpoint_cost": 0, "business_api_tested": False}
    assert stats["requests"] == 2
    assert calls[1][0] - calls[0][0] >= 1
    assert calls[0][1][1].startswith(API_ROOT + ENDPOINT_INFO + "?endpoint=")
    assert "Authorization" not in calls[0][1][2]
    assert calls[1][1][1].rstrip("?") == API_ROOT + USER_INFO
    assert calls[1][1][2]["Authorization"] == "Bearer file-fixture"
    assert all("Cookie" not in args[2] for _, args in calls)


@pytest.mark.parametrize(
    "response",
    [
        pricing(1),
        pricing(None),
        pricing("0"),
        pricing(False),
        pricing(uri="/other"),
        Response(200, {"code": 200, "data": {}}),
    ],
)
def test_unknown_or_nonzero_price_never_sends_key(tmp_path, response):
    t, _, calls, _ = transport(tmp_path, [response])
    with pytest.raises(LurkerError, match="无法确认") as error:
        t.check_credentials()
    assert error.value.code == "ENDPOINT_NOT_FREE"
    assert len(calls) == 1 and "Authorization" not in calls[0][1][2]


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(401, {"code": 401}), "AUTH_FAILED"),
        (Response(403, None), "HTTP_BLOCKED"),
        (Response(429, {"code": 429}, {"Retry-After": "1"}), "RATE_LIMITED"),
        (Response(500, None), "NETWORK_ERROR"),
        (TimeoutError("private-value"), "REQUEST_TIMEOUT"),
        (Response(200, None), "PAGE_INVALID"),
    ],
)
@pytest.mark.parametrize("at_account", [False, True])
def test_failure_no_retry_or_upstream_leak(tmp_path, response, code, at_account):
    t, _, calls, _ = transport(tmp_path, ([pricing()] if at_account else []) + [response], Config(retries=2))
    with pytest.raises(LurkerError) as error:
        t.check_credentials()
    assert error.value.code == code
    assert "private-value" not in str(error.value)
    assert len(calls) == (2 if at_account else 1)


@pytest.mark.parametrize(
    "response,code",
    [
        (account(is_active=False), "AUTH_FAILED"),
        (account(account_disabled=True), "AUTH_FAILED"),
        (account(email_verified=False), "AUTH_FAILED"),
        (account(is_active=1), "PAGE_INVALID"),
        (Response(200, {"code": 200, "data": {}}), "PAGE_INVALID"),
        (
            Response(
                200,
                {
                    "code": 200,
                    "api_key_data": {"api_key_status": 0},
                    "user_data": {"is_active": True, "account_disabled": False, "email_verified": True},
                },
            ),
            "AUTH_FAILED",
        ),
    ],
)
def test_account_validation(tmp_path, response, code):
    t, _, _, _ = transport(tmp_path, [pricing(), response])
    with pytest.raises(LurkerError) as error:
        t.check_credentials()
    assert error.value.code == code


@pytest.mark.parametrize("invalid_file", [False, True])
def test_bad_local_credentials_no_network(tmp_path, invalid_file):
    t, _, calls, _ = transport(tmp_path, [])
    t.secrets = FileSecrets(tmp_path / "key.json", {})
    if invalid_file:
        t.secrets.path.write_text('{"tikhub_api_key":""}')
        t.secrets.environ["TIKHUB_API_KEY"] = "valid-env-fixture"
    with pytest.raises(LurkerError) as error:
        t.check_credentials()
    assert error.value.code == ("CONFIG_INVALID" if invalid_file else "CREDENTIAL_MISSING")
    assert not calls


def test_output_closed_before_auth(tmp_path):
    t, _, calls, _ = transport(tmp_path, [pricing(), account()])

    def guard():
        if calls:
            raise LurkerError("OUTPUT_CLOSED")

    t.before_request = guard
    with pytest.raises(LurkerError) as error:
        t.check_credentials()
    assert error.value.code == "OUTPUT_CLOSED"
    assert len(calls) == 1


@pytest.mark.parametrize("fmt", ["jsonl", "json"])
def test_cli_output_and_no_profile_database(tmp_path, fmt):
    stream = io.StringIO()
    calls = []
    replies = [pricing(), account()]

    def factory(secrets, stats, config, **kwargs):
        def send(*args):
            calls.append(args)
            return replies.pop(0)

        return Transport(secrets, stats, config, send=send, sleep=lambda _: None, **kwargs)

    code = main(
        [
            "--install-root",
            str(tmp_path / "install"),
            "--data-root",
            str(tmp_path / "data"),
            "--format",
            fmt,
            "config",
            "check",
        ],
        secrets=FileSecrets(tmp_path / "key", {"TIKHUB_API_KEY": "fixture-only"}),
        transport_factory=factory,
        stdout=stream,
    )
    assert code == 0
    raw = stream.getvalue()
    assert all(secret not in raw for secret in ["fixture-only", "private@example", "private-name", "balance"])
    if fmt == "jsonl":
        records = [json.loads(line) for line in raw.splitlines()]
        assert [record["type"] for record in records] == ["result", "complete"]
        assert records[0]["payload"]["credential_source"] == "environment"
        assert records[1]["summary"]["requests"] == 2
    else:
        result = json.loads(raw)
        assert result["completion"]["summary"]["requests"] == 2
    assert len(calls) == 2
    assert not list((tmp_path / "data").rglob("*.sqlite*"))
    assert not list((tmp_path / "data").rglob("registry.json"))
