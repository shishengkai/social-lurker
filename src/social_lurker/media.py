import ipaddress
import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .errors import LurkerError, require
from .providers.http import check_response, client, public_https

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Audio:
    path: Path
    duration_ms: int
    size: int


def free_space(path, required):
    require(shutil.disk_usage(path).free >= required, "DISK_SPACE", "剩余磁盘空间不足，暂停媒体处理")


def run_process(args, *, timeout, heartbeat=lambda: None):
    # File-backed stderr avoids unbounded memory; content is never exposed or retained.
    import tempfile

    with tempfile.TemporaryFile() as diagnostics:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=diagnostics)
        start = time.monotonic()
        try:
            while True:
                try:
                    stdout, _ = process.communicate(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    heartbeat()
                    require(time.monotonic() - start < timeout, "PROCESS_TIMEOUT", "媒体子进程超时")
            diagnostics.seek(0, 2)
            require(
                process.returncode == 0 and diagnostics.tell() == 0,
                "MEDIA_INVALID",
                "媒体处理或完整解码校验失败",
            )
            return stdout
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def probe(path, heartbeat=lambda: None):
    executable = shutil.which("ffprobe")
    require(executable, "DEPENDENCY_MISSING", "缺少 ffprobe")
    data = json.loads(
        run_process(
            [executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            timeout=60,
            heartbeat=heartbeat,
        )
    )
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    require(audio, "UNSUPPORTED_MEDIA", "媒体没有有效音轨")
    stream = audio[0]
    try:
        duration = float(stream.get("duration") or data["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        raise LurkerError("MEDIA_INVALID", "无法确定完整音轨时长") from None
    require(duration > 0, "MEDIA_INVALID", "音轨时长无效")
    return stream, duration


class Media:
    def __init__(self, settings, http=None, vendor_root=None):
        self.settings = settings
        self.http = http or client()
        self.vendor_root = Path(vendor_root) if vendor_root else PACKAGE_ROOT / "vendor" / "wechat-decrypt"

    def download(self, url, target, heartbeat):
        partial = target.with_suffix(".part")
        limits = self.settings["media"]
        reserve = self.settings["execution"]["min_free_bytes"]
        started = time.monotonic()
        public_https(url)
        try:
            for _ in range(6):
                # Reject local destinations. Credentials are never attached to media requests.
                hostname = urlparse(url).hostname
                if not isinstance(self.http._transport, httpx.MockTransport):
                    try:
                        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
                    except OSError:
                        raise LurkerError("NETWORK_ERROR", "媒体域名解析失败", retryable=True) from None
                    trusted_cdn = hostname == "wxapp.tc.qq.com" or hostname.endswith(
                        (".zjcdn.com", ".douyinvod.com")
                    )
                    # macOS VPN fake-IP DNS uses RFC2544 addresses; TLS still verifies the pinned CDN hostname.
                    # This exception never permits RFC1918/loopback or arbitrary third-party hosts.
                    allowed = all(
                        ipaddress.ip_address(a[4][0]).is_global
                        or (
                            trusted_cdn
                            and ipaddress.ip_address(a[4][0]) in ipaddress.ip_network("198.18.0.0/15")
                        )
                        for a in addresses
                    )
                    require(allowed, "UNSAFE_URL", "拒绝内网媒体地址")
                with self.http.stream(
                    "GET", url, headers={"User-Agent": "social-lurker/0.1", "Accept-Encoding": "identity"}
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = public_https(urljoin(url, response.headers.get("location", "")))
                        continue
                    check_response(response)
                    length = response.headers.get("content-length")
                    if length:
                        require(
                            int(length) <= limits["max_source_bytes"], "MEDIA_LIMIT", "源文件超过项目上限"
                        )
                    total = 0
                    with partial.open("wb") as stream:
                        for chunk in response.iter_bytes(256 * 1024):
                            total += len(chunk)
                            require(
                                total <= limits["max_source_bytes"], "MEDIA_LIMIT", "下载源文件超过项目上限"
                            )
                            require(time.monotonic() - started < 900, "DOWNLOAD_TIMEOUT", "下载总时限已到")
                            free_space(target.parent, reserve + len(chunk))
                            stream.write(chunk)
                            heartbeat()
                    require(
                        total > 0 and (not length or total == int(length)), "MEDIA_INVALID", "媒体下载不完整"
                    )
                    partial.replace(target)
                    return target
            raise LurkerError("MEDIA_INVALID", "媒体重定向过多")
        except httpx.HTTPError:
            raise LurkerError("NETWORK_ERROR", "媒体下载失败", retryable=True) from None
        finally:
            partial.unlink(missing_ok=True)

    def prepare(self, source, directory, heartbeat=lambda: None):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        limits = self.settings["media"]
        expected = source["size"] or limits["max_source_bytes"]
        require(expected <= limits["max_source_bytes"], "MEDIA_LIMIT", "源文件超过项目上限")
        if source["duration"]:
            require(
                source["duration"] < limits["max_duration_seconds"], "MEDIA_LIMIT", "媒体时长超过项目上限"
            )
        free_space(
            directory, self.settings["execution"]["min_free_bytes"] + expected * 2 + limits["max_audio_bytes"]
        )
        media = self.download(source["url"], directory / "source.bin", heartbeat)
        if source.get("decode_key"):
            with media.open("rb") as stream:
                plaintext = stream.read(12)[4:8] == b"ftyp"
            if not plaintext:
                node = shutil.which("node")
                require(
                    node and (self.vendor_root / "decrypt.cjs").exists(),
                    "DEPENDENCY_MISSING",
                    "缺少 Node 或固定解密程序",
                )
                output = directory / "decoded.mp4"
                request_payload = {
                    "input": str(media),
                    "output": str(output),
                    "decode_key": source["decode_key"],
                }
                # Key via stdin, never process arguments, environment, or logs.
                try:
                    p = subprocess.run(
                        [node, str(self.vendor_root / "decrypt.cjs")],
                        input=json.dumps(request_payload),
                        text=True,
                        capture_output=True,
                        timeout=120,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    raise LurkerError("DECRYPT_FAILED", "视频号解密程序失败") from None
                require(
                    p.returncode == 0 and output.exists(),
                    "DECRYPT_FAILED",
                    "视频号媒体与解密 key 不匹配或解密失败",
                )
                media = output
        _, duration = probe(media, heartbeat)
        require(duration < limits["max_duration_seconds"], "MEDIA_LIMIT", "音轨时长超过项目上限")
        if source["duration"]:
            require(
                abs(source["duration"] - duration) <= max(2, source["duration"] * 0.001),
                "MEDIA_DURATION_MISMATCH",
                "实际音轨与作品标注时长不符",
            )
        binary = shutil.which("ffmpeg")
        require(binary, "DEPENDENCY_MISSING", "缺少 ffmpeg")
        free_space(directory, self.settings["execution"]["min_free_bytes"] + limits["max_audio_bytes"])
        output = directory / "audio.part"
        try:
            run_process(
                [
                    binary,
                    "-nostdin",
                    "-hide_banner",
                    "-v",
                    "error",
                    "-xerror",
                    "-y",
                    "-i",
                    str(media),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "24k",
                    "-f",
                    "mp3",
                    str(output),
                ],
                timeout=1800,
                heartbeat=heartbeat,
            )
            require(
                0 < output.stat().st_size <= limits["max_audio_bytes"],
                "MEDIA_LIMIT",
                "转换音频为空或超过项目上限",
            )
            stream, converted = probe(output, heartbeat)
            require(
                int(stream["sample_rate"]) == 16000
                and stream["channels"] == 1
                and stream["codec_name"] == "mp3",
                "MEDIA_INVALID",
                "音频格式校验失败",
            )
            require(
                abs(converted - duration) <= max(2, duration * 0.001),
                "MEDIA_DURATION_MISMATCH",
                "转换后音频时长不完整",
            )
            run_process(
                [
                    binary,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(output),
                    "-map",
                    "0:a:0",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=1800,
                heartbeat=heartbeat,
            )
            final = directory / "audio.mp3"
            output.replace(final)
            return Audio(final, round(duration * 1000), final.stat().st_size)
        finally:
            output.unlink(missing_ok=True)
