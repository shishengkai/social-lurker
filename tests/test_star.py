import pytest

from social_lurker import star
from social_lurker.errors import LurkerError


def test_invite_is_optional_and_only_install_upgrade(monkeypatch):
    monkeypatch.setattr(star, "state", lambda: ("user", True))
    assert star.invite("install_completed") is None
    monkeypatch.setattr(star, "state", lambda: ("user", False))
    assert star.invite("upgrade_completed")["account"] == "user"
    with pytest.raises(LurkerError):
        star.invite("tick")

    def unavailable():
        raise LurkerError("STAR_UNAVAILABLE", "not logged in")

    monkeypatch.setattr(star, "state", unavailable)
    assert star.invite("install_completed") is None


def test_star_needs_authorization_and_same_account(monkeypatch):
    monkeypatch.setattr(star, "state", lambda: ("changed-user", False))
    with pytest.raises(LurkerError):
        star.apply("user", False)
    with pytest.raises(LurkerError) as error:
        star.apply("user", True)
    assert error.value.code == "STAR_ACCOUNT_CHANGED"


def test_star_success_requires_readback(monkeypatch):
    from subprocess import CompletedProcess

    results = iter([("user", False), ("user", True)])
    calls = []
    monkeypatch.setattr(star, "state", lambda: next(results))
    monkeypatch.setattr(star, "gh", lambda *args: calls.append(args) or CompletedProcess(args, 0, "", ""))
    assert star.apply("user", True)["starred"]
    assert len(calls) == 1
