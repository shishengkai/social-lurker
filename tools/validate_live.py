#!/usr/bin/env python3
"""Explicit bounded development probe. Media and paid ASR each require their own switch."""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import dotenv_values

from social_lurker.config import DEFAULTS  # noqa: E402
from social_lurker.errors import LurkerError  # noqa: E402
from social_lurker.media import Media  # noqa: E402
from social_lurker.providers.fal import Fal  # noqa: E402
from social_lurker.providers.tikhub import TikHub, dy_work, wx_object, wx_work  # noqa: E402
from social_lurker.util import digest, write_json  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--media", action="store_true")
    parser.add_argument("--paid-asr", action="store_true")
    args = parser.parse_args()
    if args.paid_asr and not args.media:
        parser.error("--paid-asr requires --media")
    previous_report = args.output / "report.json"
    if args.paid_asr and previous_report.exists():
        previous = json.loads(previous_report.read_text())
        if previous.get("asr_intent"):
            parser.error("Existing ASR intent: reconcile/query its job_id; do not submit this probe again")
    args.output.mkdir(parents=True, exist_ok=True)
    creds = dotenv_values(args.credentials_file, interpolate=False)
    social = TikHub(creds["TIKHUB_API_KEY"])
    report = {"media_requested": args.media, "paid_asr_requested": args.paid_asr}
    try:
        account = social.resolve(args.source)
        report["account"] = asdict(account)
        print(
            json.dumps({"stage": "resolved", "platform": account.platform, "name": account.name}), flush=True
        )
        data = social.call(
            account.platform,
            "fetch_video_detail" if account.platform == "wechat_channels" else "fetch_one_video_by_share_url",
            {"share_url": args.source, "raw": True}
            if account.platform == "wechat_channels"
            else {"share_url": args.source},
        )
        detail = (
            wx_work(wx_object(data))
            if account.platform == "wechat_channels"
            else dy_work(data.get("aweme_detail") or {})
        )
        source = social.media_source(account.platform, detail)
        report["work"] = {
            "id": detail.id,
            "published_at": detail.published_at,
            "has_provider_source_url": bool(detail.source_url),
            "has_decode_key": bool(source["decode_key"]),
            "duration": source["duration"],
            "bytes": source["size"],
        }
        cursor, seen = None, set()
        for number in range(max(0, min(args.max_pages, 100))):
            page = social.page({"platform": account.platform, "platform_account_id": account.id}, cursor)
            seen.update(w.id for w in page.items)
            report["enumeration"] = {
                "pages": number + 1,
                "unique_works": len(seen),
                "has_more": page.has_more,
                "has_cursor": bool(page.cursor),
                "cursor_repeated": page.cursor == cursor,
                "missing_publication_count_last_page": sum(w.published_at is None for w in page.items),
            }
            print(json.dumps({"stage": "page", **report["enumeration"]}), flush=True)
            if page.has_more is False or not page.cursor or page.cursor == cursor:
                break
            cursor = page.cursor
        if args.media:
            audio = Media(DEFAULTS).prepare(source, args.output / "work")
            report["audio"] = {
                "duration_ms": audio.duration_ms,
                "bytes": audio.size,
                "format": "mp3/16000Hz/mono/24kbps",
            }
            print(json.dumps({"stage": "media_validated", **report["audio"]}), flush=True)
            if args.paid_asr:
                asr = Fal(creds["FAL_KEY"])
                url = asr.upload(audio.path)
                report["upload"] = {"requested_retention_seconds": 172800, "actual_expiry_verified": False}
                report["asr_intent"] = {"state": "submitting"}
                write_json(args.output / "report.json", report)
                job = asr.submit(url)
                report["asr_intent"] = {"state": "submitted", "job_id": job}
                write_json(args.output / "report.json", report)
                for _ in range(48):
                    state = asr.query(job)
                    if state == "COMPLETED":
                        text = asr.fetch_result(job)
                        # Durable transcript is SQLite-only, including live probes.
                        import sqlite3

                        with sqlite3.connect(args.output / "transcript.sqlite3") as db:
                            db.execute(
                                "CREATE TABLE IF NOT EXISTS transcript (job_id TEXT PRIMARY KEY, text TEXT NOT NULL)"
                            )
                            db.execute("INSERT OR REPLACE INTO transcript VALUES(?,?)", (job, text))
                        report["asr"] = {
                            "state": "completed",
                            "characters": len(text),
                            "sha256": digest(text),
                            "model": "fal-ai/whisper",
                            "native_proofreading": "not_tested",
                        }
                        import shutil

                        shutil.rmtree(args.output / "work")
                        break
                    time.sleep(10)
                else:
                    report["asr"] = {"state": "pending", "job_id": job}
        report["ok"] = True
    except LurkerError as error:
        report["ok"], report["error"] = False, error.public()
    except Exception as error:
        report["ok"], report["error"] = False, {"code": "INTERNAL_ERROR", "type": type(error).__name__}
    write_json(args.output / "report.json", report)
    # Source media URLs, keys, API response payloads and transcript never reach stdout.
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
