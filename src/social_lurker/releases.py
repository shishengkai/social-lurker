"""Fixed-authority immutable releases; no Git mutation and no fallback to older tags."""

import io
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

from .errors import LurkerError, require
from .util import digest, parse_json, safe_path, semver

AUTHORITY = "github.com/shishengkai/social-lurker"
API = "https://api.github.com/repos/shishengkai/social-lurker"


def fetch(url, limit=32 * 1024 * 1024):
    require(
        url.startswith(
            (
                "https://api.github.com/repos/shishengkai/social-lurker/",
                "https://github.com/shishengkai/social-lurker/releases/download/",
            )
        ),
        "RELEASE_SOURCE_INVALID",
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "social-lurker-r1", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(limit + 1)
        require(len(data) <= limit, "RELEASE_TOO_LARGE")
        return data
    except OSError:
        raise LurkerError("RELEASE_UNAVAILABLE", "无法核验官方稳定发布") from None


def discover(*, read=fetch):
    try:
        return _discover(read=read)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise LurkerError("RELEASE_INVALID", "最高正式发布的信息无法可靠核验") from None


def _discover(*, read=fetch):
    releases = []
    page = 1
    while True:
        batch = parse_json(read(API + f"/releases?per_page=100&page={page}"), 8 * 1024 * 1024)
        require(isinstance(batch, list), "RELEASE_INVALID")
        releases += batch
        if len(batch) < 100:
            break
        page += 1
    candidates = [
        r
        for r in releases
        if not r.get("draft")
        and not r.get("prerelease")
        and re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", r.get("tag_name", ""))
    ]
    require(candidates, "NO_STABLE_RELEASE")
    chosen = max(candidates, key=lambda r: semver(r["tag_name"][1:]))
    require(chosen.get("immutable") is True, "RELEASE_MUTABLE", "最高正式版本尚未锁定，不回退选择旧版本")
    obj = parse_json(read(API + "/git/ref/tags/" + chosen["tag_name"]))["object"]
    seen = set()
    while obj["type"] == "tag":
        require(obj["sha"] not in seen, "RELEASE_INVALID")
        seen.add(obj["sha"])
        obj = parse_json(read(API + "/git/tags/" + obj["sha"]))["object"]
    require(obj["type"] == "commit" and re.fullmatch("[0-9a-f]{40}", obj["sha"]), "RELEASE_INVALID")
    assets = {a["name"]: a for a in chosen.get("assets", [])}
    require("release-manifest.json" in assets, "RELEASE_MANIFEST_MISSING")
    descriptor = parse_json(read(assets["release-manifest.json"]["browser_download_url"]))
    manifest = validate_manifest(descriptor["manifest"])
    require(
        manifest["version"] == chosen["tag_name"][1:] and manifest["source_commit"] == obj["sha"],
        "RELEASE_COMMIT_MISMATCH",
    )
    name = "social-lurker-" + manifest["version"] + ".tar.gz"
    require(name in assets, "RELEASE_ARCHIVE_MISSING")
    require(re.fullmatch("[0-9a-f]{64}", descriptor.get("archive_sha256", "")), "RELEASE_DIGEST_INVALID")
    return {**descriptor, "archive_url": assets[name]["browser_download_url"]}


def validate_manifest(m):
    require(
        isinstance(m, dict)
        and m.get("authority") == AUTHORITY
        and m.get("product") == "social-lurker-lightweight",
        "RELEASE_IDENTITY_INVALID",
    )
    semver(m.get("version"))
    require(
        m.get("channel") == "stable" and m.get("schema_version") == 1 and m.get("protocol") == 1,
        "RELEASE_INCOMPATIBLE",
    )
    require(re.fullmatch("[0-9a-f]{40}", m.get("source_commit", "")), "RELEASE_COMMIT_INVALID")
    require(isinstance(m.get("files"), dict) and m["files"], "RELEASE_INVALID")
    for name, hashed in m["files"].items():
        p = PurePosixPath(name)
        require(
            not p.is_absolute() and ".." not in p.parts and str(p) == name and "\\" not in name,
            "RELEASE_PATH_INVALID",
        )
        require(
            name.startswith(("social_lurker/", "skills/")) or name in {"run.py", "LICENSE"},
            "RELEASE_PATH_INVALID",
        )
        require(re.fullmatch("[0-9a-f]{64}", hashed), "RELEASE_DIGEST_INVALID")
    require(
        {
            "social_lurker/__init__.py",
            "social_lurker/cli.py",
            "social_lurker/schema.sql",
            "run.py",
            "skills/social-lurker/SKILL.md",
            "skills/social-lurker-maintainer/SKILL.md",
        }
        <= set(m["files"]),
        "RELEASE_INCOMPLETE",
    )
    return m


def package_bytes(source):
    source = Path(source)
    data = {}
    for p in sorted((source / "src/social_lurker").rglob("*")):
        if p.is_file() and p.suffix in {".py", ".sql"}:
            require(not p.is_symlink(), "RELEASE_PATH_INVALID")
            data["social_lurker/" + str(p.relative_to(source / "src/social_lurker"))] = p.read_bytes()
    for p in sorted((source / "skills").rglob("*.md")):
        require(not p.is_symlink(), "RELEASE_PATH_INVALID")
        data[str(p.relative_to(source))] = p.read_bytes()
    data["run.py"] = (source / "tools/launcher.py").read_bytes()
    data["LICENSE"] = (source / "LICENSE").read_bytes()
    return data


def verify_directory(folder, manifest):
    manifest = validate_manifest(manifest)
    folder = safe_path(folder)
    expected = set(manifest["files"]) | {"manifest.json"}
    require(not any(p.is_symlink() for p in folder.rglob("*")), "RELEASE_PATH_INVALID")
    actual = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
    require(actual == expected, "RELEASE_FILES_MISMATCH")
    for name, hashed in manifest["files"].items():
        p = safe_path(folder / name)
        require(
            p.is_relative_to(folder) and p.is_file() and digest(p.read_bytes()) == hashed,
            "RELEASE_DIGEST_INVALID",
        )
    init = (folder / "social_lurker/__init__.py").read_text()
    require(
        re.search(r"__version__\s*=\s*[\"\x27]" + re.escape(manifest["version"]) + r"[\"\x27]", init),
        "RELEASE_VERSION_MISMATCH",
    )
    require(parse_json((folder / "manifest.json").read_bytes()) == manifest, "RELEASE_MANIFEST_MISMATCH")
    return manifest


def prepare(instance, descriptor, *, read=fetch):
    manifest = validate_manifest(descriptor["manifest"])
    destination = instance.path("app/" + manifest["version"])
    if destination.exists():
        verify_directory(destination, manifest)
        return destination
    raw = read(descriptor["archive_url"])
    require(digest(raw) == descriptor["archive_sha256"], "RELEASE_DIGEST_INVALID")
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".package-", dir=destination.parent))
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            seen = set()
            total = 0
            for member in members:
                require(
                    member.isfile()
                    and member.name not in seen
                    and member.name in set(manifest["files"]) | {"manifest.json"},
                    "RELEASE_PATH_INVALID",
                )
                seen.add(member.name)
                total += member.size
                require(total <= 32 * 1024 * 1024, "RELEASE_TOO_LARGE")
                target = safe_path(stage / member.name)
                require(target.is_relative_to(stage), "RELEASE_PATH_INVALID")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as f:
                    f.write(archive.extractfile(member).read())
        verify_directory(stage, manifest)
        for p in stage.rglob("*"):
            if p.is_file():
                p.chmod(0o400)
        stage.rename(destination)
        from .util import fsync_dir

        fsync_dir(destination.parent)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)
