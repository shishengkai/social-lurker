"""Caller-named profiles; opaque Unicode IDs are never filesystem paths."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import LurkerError, require
from .locks import lock
from .util import atomic_json, read_json


def valid_profile_id(value):
    if not isinstance(value, str) or not 0 < len(value) <= 1024:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def storage_name(profile_id):
    return "id-" + hashlib.sha256(profile_id.encode("utf-8")).hexdigest()


def legacy_id(value):
    return (
        bool(re.fullmatch(r"p[0-9]{4,}", value)) and value == f"p{int(value[1:]):04d}" and int(value[1:]) > 0
    )


@dataclass(frozen=True)
class Profile:
    profile_id: str
    label: str
    db_path: Path

    @property
    def lock_path(self):
        return self.db_path.parent / "operation.lock"

    @property
    def backup_name(self):
        return self.db_path.parent.name + ".sqlite"


class ProfileRegistry(Protocol):
    def get(self, profile_id: str) -> Profile: ...
    def list(self) -> list[Profile]: ...
    def create(self, profile_id: str, label: str | None = None) -> Profile: ...


class FileRegistry:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "profiles.json"

    def load(self, *, allow_new=False):
        if not self.path.exists():
            require(allow_new and not (self.root / "profiles").exists(), "CONFIG_INVALID")
            return {"schema_version": 2, "profiles": []}
        value = read_json(self.path)
        require(
            isinstance(value, dict)
            and type(value.get("schema_version")) is int
            and value["schema_version"] in (1, 2)
            and isinstance(value.get("profiles"), list),
            "CONFIG_INVALID",
        )
        if value["schema_version"] == 1:
            require(type(value.get("next_id")) is int and value["next_id"] >= 1, "CONFIG_INVALID")
        seen, paths = set(), set()
        for item in value["profiles"]:
            require(
                isinstance(item, dict)
                and valid_profile_id(item.get("id"))
                and item["id"] not in seen
                and valid_profile_id(item.get("label")),
                "CONFIG_INVALID",
            )
            pid = item["id"]
            old = legacy_id(pid)
            expected = {f"profiles/{storage_name(pid)}/state.sqlite"}
            if old:
                expected.add(f"profiles/{pid}/state.sqlite")
            if value["schema_version"] == 1:
                require(old and int(pid[1:]) < value["next_id"], "CONFIG_INVALID")
                expected = {f"profiles/{pid}/state.sqlite"}
            require(item.get("db") in expected and item["db"] not in paths, "CONFIG_INVALID")
            seen.add(pid)
            paths.add(item["db"])
        return value

    def profile(self, item):
        path = self.root / item["db"]
        require(path.resolve() == path and self.root in path.parents, "CONFIG_INVALID")
        return Profile(item["id"], item["label"], path)

    def list(self):
        return [self.profile(item) for item in self.load()["profiles"]]

    def get(self, profile_id):
        require(valid_profile_id(profile_id), "INPUT_INVALID")
        for profile in self.list():
            if profile.profile_id == profile_id:
                return profile
        raise LurkerError("PROFILE_NOT_FOUND")

    def create(self, profile_id, label=None):
        require(valid_profile_id(profile_id), "INPUT_INVALID")
        if label is not None:
            require(
                isinstance(label, str)
                and bool(label.strip())
                and len(label) <= 256
                and not any(ord(c) < 32 for c in label),
                "INPUT_INVALID",
            )
        with lock(self.root / "registry.lock"):
            data = self.load(allow_new=True)
            for existing in data["profiles"]:
                if existing["id"] == profile_id:
                    require(label is None or label == existing["label"], "INPUT_INVALID")
                    return self.profile(existing)
            item = {
                "id": profile_id,
                "label": profile_id if label is None else label,
                "db": f"profiles/{storage_name(profile_id)}/state.sqlite",
            }
            profile = self.profile(item)
            require(not profile.db_path.parent.exists(), "CONFIG_INVALID")
            data["profiles"].append(item)
            data["schema_version"] = 2
            data.pop("next_id", None)
            atomic_json(self.path, data)
            return profile
