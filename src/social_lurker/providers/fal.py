from pathlib import Path
from urllib.parse import quote

import httpx

from ..errors import LurkerError, require
from ..util import json_text
from .http import client, object_json, public_https, request

MODEL = "fal-ai/whisper"
QUEUE = "https://queue.fal.run/" + MODEL
LIFECYCLE = json_text({"expiration_duration_seconds": 172800})


class Fal:
    def __init__(self, key, http=None):
        require(bool(key), "CREDENTIALS_MISSING", "请在当前实例 .env 配置 FAL_KEY")
        self.http = http or client()
        self.headers = {"Authorization": "Key " + key}

    def upload(self, path, heartbeat=lambda: None):
        path = Path(path)
        # Explicit single-attempt HTTP implementation of the official CDN protocol.
        auth = object_json(
            request(
                self.http,
                "POST",
                "https://rest.fal.ai/storage/auth/token?storage_type=fal-cdn-v3",
                headers=self.headers,
                json={},
            )
        )
        require(
            all(isinstance(auth.get(k), str) and auth[k] for k in ("token", "token_type", "base_url")),
            "UPLOAD_INVALID",
            "CDN 认证响应无效",
        )
        base = public_https(auth["base_url"].rstrip("/"), ["fal.media"])
        headers = {
            "Authorization": auth["token_type"] + " " + auth["token"],
            "Content-Type": "audio/mpeg",
            "X-Fal-File-Name": "audio.mp3",
            "X-Fal-Object-Lifecycle-Preference": LIFECYCLE,
        }
        if path.stat().st_size < 100 * 1024 * 1024:

            def chunks():
                with path.open("rb") as stream:
                    while chunk := stream.read(256 * 1024):
                        heartbeat()
                        yield chunk

            response = request(
                self.http,
                "POST",
                base + "/files/upload",
                headers=headers | {"Content-Length": str(path.stat().st_size)},
                content=chunks(),
            )
            url = object_json(response).get("access_url")
            return public_https(url, ["fal.media"])
        initiated = object_json(request(self.http, "POST", base + "/files/upload/multipart", headers=headers))
        url = public_https(initiated.get("access_url"), ["fal.media"])
        upload_id = initiated.get("uploadId")
        require(isinstance(upload_id, str) and upload_id, "UPLOAD_INVALID", "CDN 缺少上传标识")
        parts = []
        with path.open("rb") as stream:
            number = 1
            while chunk := stream.read(10 * 1024 * 1024):
                heartbeat()
                response = request(
                    self.http,
                    "PUT",
                    f"{url}/multipart/{quote(upload_id, safe='')}/{number}",
                    headers=headers | {"Accept-Encoding": "identity"},
                    content=chunk,
                )
                require("etag" in response.headers, "UPLOAD_INVALID", "CDN 分块回执缺失")
                parts.append({"partNumber": number, "etag": response.headers["etag"]})
                number += 1
        request(
            self.http,
            "POST",
            f"{url}/multipart/{quote(upload_id, safe='')}/complete",
            headers=headers,
            json={"parts": parts},
        )
        return url

    def submit(self, audio_url):
        public_https(audio_url, ["fal.media"])
        try:
            response = self.http.post(
                QUEUE,
                headers=self.headers
                | {"X-Fal-No-Retry": "1", "X-Fal-Object-Lifecycle-Preference": LIFECYCLE},
                json={
                    "audio_url": audio_url,
                    "task": "transcribe",
                    "language": None,
                    "diarize": False,
                    "chunk_level": "segment",
                    "prompt": "",
                },
            )
        except httpx.HTTPError:
            raise LurkerError("ASR_SUBMIT_UNKNOWN", "请求可能已提交，禁止自动重新提交") from None
        # Only explicit client-side rejection proves no job was accepted. 5xx/invalid success are uncertain.
        if response.status_code in (400, 401, 402, 403, 422, 429):
            from .http import check_response

            check_response(response)
        if not 200 <= response.status_code < 300:
            raise LurkerError("ASR_SUBMIT_UNKNOWN", "ASR 提交未取得可靠回执")
        try:
            data = response.json()
            job = data.get("request_id")
            require(isinstance(job, str) and bool(job), "ASR_SUBMIT_UNKNOWN", "ASR 任务标识缺失")
        except (ValueError, AttributeError):
            raise LurkerError("ASR_SUBMIT_UNKNOWN", "ASR 提交回执无效") from None
        return job

    def query(self, job_id):
        data = object_json(
            request(
                self.http,
                "GET",
                QUEUE + "/requests/" + quote(job_id, safe="") + "/status",
                headers=self.headers,
            )
        )
        status = data.get("status")
        require(status in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), "ASR_STATUS_INVALID", "ASR 状态无法确认")
        return status

    def fetch_result(self, job_id):
        data = object_json(
            request(self.http, "GET", QUEUE + "/requests/" + quote(job_id, safe=""), headers=self.headers)
        )
        require(
            "error" not in data and isinstance(data.get("text"), str),
            "ASR_RESULT_INVALID",
            "ASR 成功结果缺少有效 text",
        )
        # Keep the provider's complete text byte-for-byte. Chunk timing cannot justify deleting text.
        return data["text"]
