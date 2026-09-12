import pytest
from conftest import ingest, publication

from social_lurker.cli import execute
from social_lurker.config import automatic_gate
from social_lurker.delivery import Delivery
from social_lurker.errors import LurkerError
from social_lurker.operations import bind, check
from social_lurker.util import atomic_json


def test_user_length_policy_allows_foreground_trial_but_does_not_verify_host(instance, watch, clock):
    result = bind(
        instance,
        {
            "evidence": "fixture-user-choice",
            "host": {
                "max_message_length": 4000,
                "length_unit": "unicode",
                "length_basis": "user_selected",
                "length_evidence": "fixture-user-request-4000",
            },
        },
    )
    assert result["test_ready"] and result["mode"] == "foreground_only"
    assert result["message_length"] == {
        "limit": 4000,
        "unit": "unicode",
        "basis": "user_selected",
        "verified": False,
        "scope": "foreground_only",
    }
    assert "HOST_LENGTH_UNVERIFIED" in result["missing"]
    assert not result["routine"]["active"]
    with pytest.raises(LurkerError, match="HOST_LENGTH_UNVERIFIED"):
        automatic_gate(instance.load())
    ingest(instance, watch, [publication(watch, 1, clock.now)])
    with instance.transaction() as (db, _):
        db.execute("UPDATE updates SET reason='test'")
    assert Delivery(instance).next(foreground_test=True)["payload"]["text"]


@pytest.mark.parametrize("basis", [None, [], "assumed"])
def test_legacy_or_invalid_length_evidence_does_not_become_verified(instance, basis):
    settings = instance.load()
    settings["host"]["evidence_ref"]["host"]["length_basis"] = basis
    atomic_json(instance.path("settings.json"), settings)
    result = check(instance)
    assert not result["test_ready"] and not result["message_length"]["verified"]
    assert result["message_length"]["scope"] == "unverified"


def test_length_classification_cannot_relabel_old_proof_without_full_new_binding(instance):
    original = instance.path("settings.json").read_bytes()
    with pytest.raises(LurkerError, match="LENGTH_PROOF_REQUIRED"):
        bind(instance, {"evidence": "fixture", "host": {"length_basis": "measured"}})
    assert instance.path("settings.json").read_bytes() == original
    result = bind(
        instance,
        {
            "evidence": "fixture-length-experiment",
            "host": {
                "max_message_length": 1000,
                "length_unit": "utf16",
                "length_basis": "measured",
                "length_evidence": "fixture-accepted-payload-and-provider-receipt",
            },
        },
    )
    assert result["message_length"]["verified"]


def test_native_commands_always_use_automatic_and_reject_downgrade_flags(instance, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "social_lurker.monitor.Monitor.poll",
        lambda self, **kw: calls.append(("poll", kw)) or {"quiet": True},
    )
    monkeypatch.setattr("social_lurker.delivery.Delivery.next", lambda self, **kw: calls.append(("next", kw)))
    for action in ("poll", "next"):
        execute(instance, ("routine", action), {"protocol": 1})
        with pytest.raises(LurkerError, match="INPUT_INVALID"):
            execute(instance, ("routine", action), {"protocol": 1, "automatic": False})
    assert calls == [("poll", {"automatic": True}), ("next", {"automatic": True})]


def test_wake_probe_needs_no_credentials_and_cannot_change_state_or_certify_silence(instance, monkeypatch):
    instance.path(".env").unlink()
    before = {n: instance.path(n).read_bytes() for n in ("settings.json", "state.sqlite")}
    for path in (
        "social_lurker.monitor.Monitor.poll",
        "social_lurker.delivery.Delivery.next",
        "social_lurker.http.Client.call",
    ):
        monkeypatch.setattr(path, lambda *a, **kw: pytest.fail("wake probe invoked business work"))
    result = execute(instance, ("routine", "probe"), {"protocol": 1, "probe_id": "fixture-probe-1"})
    assert result["probe_id"] == "fixture-probe-1" and result["kind"] == "host_wake_probe"
    assert result["bound_routine_id"] == instance.load()["host"]["routine_id"]
    assert "routine_id" not in result  # The program cannot identify the actual native trigger.
    assert result["data_requests"] == result["messages_sent"] == 0
    assert not {"silent", "verified", "automatic_ready"} & set(result)
    assert before == {n: instance.path(n).read_bytes() for n in before}
    with pytest.raises(LurkerError, match="INPUT_INVALID"):
        execute(instance, ("routine", "probe"), {"protocol": 1, "probe_id": "x", "automatic": False})
