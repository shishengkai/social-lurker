"""Stable automatic IDs; callers must choose explicitly. Labels are never paths."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import LurkerError, require
from .locks import lock
from .util import atomic_json, read_json


@dataclass(frozen=True)
class Profile:
    profile_id: str
    label: str
    db_path: Path

    @property
    def lock_path(self):
        return self.db_path.parent / "operation.lock"


class ProfileRegistry(Protocol):
    def get(self, profile_id: str) -> Profile: ...
    def list(self) -> list[Profile]: ...
    def create(self, label: str) -> Profile: ...


class FileRegistry:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "profiles.json"

    def load(self, *, allow_new=False):
        if not self.path.exists():
            # Never reconstruct a lost registry by discovering existing databases.
            require(allow_new and not (self.root / "profiles").exists(), "CONFIG_INVALID")
            return {"schema_version": 1, "next_id": 1, "profiles": []}
        value = read_json(self.path)
        require(
            isinstance(value, dict)
            and value.get("schema_version") == 1
            and type(value.get("next_id")) is int
            and value["next_id"] >= 1
            and isinstance(value.get("profiles"), list),
            "CONFIG_INVALID",
        )
        seen = set()
        for item in value["profiles"]:
            require(
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and re.fullmatch(r"p[0-9]{4,}", item["id"])
                and 0 < int(item["id"][1:]) < value["next_id"]
                and item["id"] not in seen
                and isinstance(item.get("label"), str)
                and bool(item["label"]),
                "CONFIG_INVALID",
            )
            expected = f"profiles/{item['id']}/state.sqlite"
            require(item["id"] == f"p{int(item['id'][1:]):04d}", "CONFIG_INVALID")
            require(item.get("db") == expected, "CONFIG_INVALID")
            seen.add(item["id"])
        return value

    def profile(self, item):
        path = self.root / item["db"]
        require(path.resolve() == path and self.root in path.parents, "CONFIG_INVALID")
        return Profile(item["id"], item["label"], path)

    def list(self):
        return [self.profile(item) for item in self.load()["profiles"]]

    def get(self, profile_id):
        require(isinstance(profile_id, str) and re.fullmatch(r"p[0-9]{4,}", profile_id), "INPUT_INVALID")
        for profile in self.list():
            if profile.profile_id == profile_id:
                return profile
        raise LurkerError("PROFILE_NOT_FOUND")

    def create(self, label):
        require(
            isinstance(label, str)
            and bool(label.strip())
            and len(label) <= 256
            and not any(ord(c) < 32 for c in label),
            "INPUT_INVALID",
        )
        with lock(self.root / "registry.lock"):
            data = self.load(allow_new=True)
            pid = f"p{data['next_id']:04d}"
            item = {"id": pid, "label": label, "db": f"profiles/{pid}/state.sqlite"}
            profile = self.profile(item)
            require(not profile.db_path.parent.exists(), "CONFIG_INVALID")
            # Register atomically before a business command creates its database.
            data["profiles"].append(item)
            data["next_id"] += 1
            atomic_json(self.path, data)
            return profile
