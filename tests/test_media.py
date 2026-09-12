import copy
import hashlib
import json
import shutil
import subprocess

import httpx
import pytest

from social_lurker.config import DEFAULTS
from social_lurker.errors import LurkerError
from social_lurker.media import Media
from social_lurker.providers.fal import Fal


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe required"
)
def test_real_mp3_conversion_and_duration(tmp_path):
    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "3",
            str(source),
        ],
        check=True,
    )

    class Local(Media):
        def download(self, url, target, heartbeat):
            shutil.copyfile(source, target)
            return target

    audio = Local(copy.deepcopy(DEFAULTS)).prepare(
        {
            "url": "https://example.org/audio",
            "size": source.stat().st_size,
            "duration": 3,
            "decode_key": None,
        },
        tmp_path / "work",
    )
    assert abs(audio.duration_ms - 3000) <= 10
    assert audio.path.suffix == ".mp3" and audio.size < source.stat().st_size


def test_partial_download_never_becomes_cache(tmp_path):
    settings = copy.deepcopy(DEFAULTS)
    settings["media"]["max_source_bytes"] = 3
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345"))
    media = Media(settings, httpx.Client(transport=transport))
    target = tmp_path / "source.bin"
    with pytest.raises(LurkerError):
        media.download("https://example.org/media", target, lambda: None)
    assert not target.exists() and not target.with_suffix(".part").exists()


def test_cdn_upload_uses_current_instance_token_retention_and_no_retry(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "rest.fal.ai":
            assert request.headers["authorization"] == "Key instance-key"
            return httpx.Response(
                200, json={"token": "cdn-token", "token_type": "Bearer", "base_url": "https://v3.fal.media"}
            )
        assert request.headers["authorization"] == "Bearer cdn-token"
        assert json.loads(request.headers["X-Fal-Object-Lifecycle-Preference"]) == {
            "expiration_duration_seconds": 172800
        }
        assert request.content == b"audio"
        return httpx.Response(200, json={"access_url": "https://v3.fal.media/uploaded.mp3"})

    fal = Fal("instance-key", httpx.Client(transport=httpx.MockTransport(respond)))
    assert fal.upload(audio) == "https://v3.fal.media/uploaded.mp3"
    assert len(calls) == 2


def test_vendor_matches_pinned_hashes():
    from pathlib import Path

    directory = Path("vendor/wechat-decrypt")
    provenance = json.loads((directory / "provenance.json").read_text())
    assert provenance["commit"] == "799a131834ccc6ac69d1fbfa8c30350c4f718ccb"
    for name, expected in provenance["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
