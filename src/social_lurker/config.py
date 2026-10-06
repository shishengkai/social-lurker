import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import LurkerError, require
from .util import atomic_json, read_json

DEFAULT_INSTALL = Path.home() / ".local/opt/social-lurker-cli"
DEFAULT_DATA = Path.home() / ".local/share/social-lurker-cli"
DEFAULT_CREDENTIALS = Path.home() / ".config/social-lurker-cli/credentials.json"


def valid_key(key):
    return isinstance(key, str) and bool(key) and len(key) <= 4096 and all(33 <= ord(c) < 127 for c in key)


class SecretSource(Protocol):
    def get_key(self) -> str: ...


class FileSecrets:
    def __init__(self, path=DEFAULT_CREDENTIALS, environ=None):
        self.path = Path(path).expanduser().resolve()
        self.environ = os.environ if environ is None else environ

    def load(self):
        if not self.path.exists():
            return {}
        require(self.path.is_file(), "CONFIG_INVALID")
        data = read_json(self.path)
        require(isinstance(data, dict), "CONFIG_INVALID")
        return data

    def get_key(self):
        data = self.load()
        if "tikhub_api_key" in data:
            key = data["tikhub_api_key"]
            require(valid_key(key), "CONFIG_INVALID")
            return key
        key = self.environ.get("TIKHUB_API_KEY")
        if key is None:
            raise LurkerError("CREDENTIAL_MISSING")
        require(valid_key(key), "CONFIG_INVALID")
        return key

    def set_key(self, key):
        require(valid_key(key), "CONFIG_INVALID")
        data = self.load()
        data["tikhub_api_key"] = key
        atomic_json(self.path, data, 0o600)

    def status(self):
        data = self.load()
        if "tikhub_api_key" in data:
            require(valid_key(data["tikhub_api_key"]), "CONFIG_INVALID")
            return {"configured": True, "source": "file"}
        key = self.environ.get("TIKHUB_API_KEY")
        require(key is None or valid_key(key), "CONFIG_INVALID")
        return {"configured": key is not None, "source": "environment" if key is not None else None}


@dataclass(frozen=True)
class Config:
    rps: float = 1.0
    timeout: float = 60.0
    retries: int = 0

    def validate(self):
        require(0 < self.rps <= 10 and 0 < self.timeout <= 120 and 0 <= self.retries <= 2, "CONFIG_INVALID")
