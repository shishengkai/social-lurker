import json

import pytest

from social_lurker.errors import LurkerError
from social_lurker.releases import API, AUTHORITY, discover


def manifest(version="0.3.0"):
    return {
        "authority": AUTHORITY,
        "product": "social-lurker-lightweight",
        "version": version,
        "channel": "stable",
        "protocol": 1,
        "schema_version": 1,
        "source_commit": "a" * 40,
        "files": {
            p: "b" * 64
            for p in (
                "social_lurker/__init__.py",
                "social_lurker/cli.py",
                "social_lurker/schema.sql",
                "run.py",
                "skills/social-lurker/SKILL.md",
                "skills/social-lurker-maintainer/SKILL.md",
            )
        },
    }


def release(version, **extra):
    prefix = f"https://github.com/shishengkai/social-lurker/releases/download/v{version}/"
    return {
        "tag_name": "v" + version,
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "assets": [
            {"name": n, "browser_download_url": prefix + n}
            for n in ("release-manifest.json", f"social-lurker-{version}.tar.gz")
        ],
        **extra,
    }


def fake_reader(releases, metadata=None, tag_type="commit"):
    calls = []

    def read(url):
        calls.append(url)
        if url.startswith(API + "/releases?"):
            value = releases
        elif "/git/ref/tags/" in url:
            value = {"object": {"type": tag_type, "sha": "c" * 40 if tag_type == "tag" else "a" * 40}}
        elif "/git/tags/" in url:
            value = {"object": {"type": "commit", "sha": "a" * 40}}
        elif url.endswith("release-manifest.json"):
            value = metadata or {"manifest": manifest(), "archive_sha256": "d" * 64}
        else:
            raise AssertionError(url)
        return json.dumps(value).encode()

    return read, calls


def test_semver_highest_and_annotated_commit_resolution():
    read, calls = fake_reader(
        [release("0.2.9"), release("0.3.0"), release("9.0.0", prerelease=True)], tag_type="tag"
    )
    assert discover(read=read)["manifest"]["version"] == "0.3.0"
    assert any("/git/tags/" in c for c in calls) and not any("v0.2.9" in c for c in calls)


@pytest.mark.parametrize("extra", [{"immutable": False}, {"assets": []}])
def test_invalid_highest_never_falls_back(extra):
    read, calls = fake_reader([release("0.3.0"), release("0.4.0", **extra)])
    with pytest.raises(LurkerError):
        discover(read=read)
    assert not any("/v0.3.0/" in c for c in calls)


def test_tag_manifest_commit_and_identity_must_match():
    m = manifest()
    m["source_commit"] = "b" * 40
    read, _ = fake_reader([release("0.3.0")], {"manifest": m, "archive_sha256": "d" * 64})
    with pytest.raises(LurkerError, match="RELEASE_COMMIT_MISMATCH"):
        discover(read=read)
    m["source_commit"] = "a" * 40
    m["product"] = "social-lurker-fulltext"
    with pytest.raises(LurkerError, match="RELEASE_IDENTITY_INVALID"):
        discover(read=read)


def test_corrupt_discovery_quiet_check_stays_silent(instance, monkeypatch):
    import social_lurker.cli as cli

    read, _ = fake_reader([release("0.3.0")], {"unexpected": "shape"})
    monkeypatch.setattr(cli, "discover", lambda: discover(read=read))
    assert cli.execute(instance, ("upgrade", "check"), {"protocol": 1, "quiet": True}) is None
