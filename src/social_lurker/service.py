"""Page transactions before output; no durable cursor, delivery state, or replay."""

from dataclasses import asdict

from .adapters.base import Author
from .errors import LurkerError, require


class Service:
    def __init__(self, db, adapters, output, stats):
        self.db, self.adapters, self.output, self.stats = db, adapters, output, stats

    def add(self, link, adapter):
        author = adapter.resolve_author(link)
        author.validate()
        old = self.db.author(author.platform, author.author_id)
        if old and old["status"] == "active":
            return {
                "platform": author.platform,
                "author_id": author.author_id,
                "author_name": old["author_name"],
                "status": "active",
                "changed": False,
                "baseline_count": 0,
            }
        page = adapter.fetch_page(author, None)
        items = page.validated(author)
        self.db.known(author, items)  # Validate cross-author conflicts before changing active state.
        self.db.upsert_page(author, items, baseline=True)
        self.stats["pages_committed"] += 1
        return {
            "platform": author.platform,
            "author_id": author.author_id,
            "author_name": author.name,
            "status": "active",
            "changed": True,
            "baseline_count": len(items),
        }

    def list(self, all_authors=False):
        fields = (
            "platform",
            "author_id",
            "author_name",
            "status",
            "works_count",
            "last_check_at",
            "last_success_at",
            "last_error_code",
        )
        return {"authors": [{k: row[k] for k in fields} for row in self.db.authors(all_authors)]}

    def check(self):
        authors = self.db.authors()
        self.stats["authors_total"] = len(authors)
        self.stats["authors_unchecked"] = len(authors)
        for row in authors:
            self.output.ensure_open()
            author = Author(row["platform"], row["author_id"], row["author_name"], row["profile_url"])
            self.stats["authors_unchecked"] -= 1
            self.db.mark(author, "start")
            try:
                self.scan(author, self.adapters[author.platform])
                self.db.mark(author, "success")
                self.stats["authors_succeeded"] += 1
            except LurkerError as error:
                if error.code in {"DB_ERROR", "OUTPUT_CLOSED", "INTERRUPTED"}:
                    self.stats["authors_failed"] += 1
                    raise
                self.db.mark(author, "error", error.code)
                self.stats["authors_failed"] += 1
                self.output.record(
                    "author_error", platform=author.platform, author_id=author.author_id, error=error.public()
                )
                if error.global_stop:
                    break
        if self.stats["authors_failed"] or self.stats["authors_unchecked"]:
            return ("partial", 3) if self.stats["authors_succeeded"] else ("error", 1)
        return "ok", 0

    def scan(self, author, adapter):
        cursor, cursors, signatures = None, set(), set()
        while True:
            self.output.ensure_open()
            page = adapter.fetch_page(author, cursor)
            items = page.validated(author)
            signature = tuple(sorted([item.work_id for item in items] + list(page.excluded_ids)))
            require(signature not in signatures, "CURSOR_INVALID")
            signatures.add(signature)
            if page.has_more:
                require(page.next_cursor != cursor and page.next_cursor not in cursors, "CURSOR_INVALID")
                cursors.add(page.next_cursor)
            known = self.db.known(author, items)
            all_new = bool(items) and not known and not page.excluded_ids
            new_ids = {item.work_id for item in items if item.work_id not in known}
            enriched, global_error = [], None
            for item in items:
                if item.work_id in new_ids and global_error is None:
                    try:
                        value = adapter.enrich_metadata(author, item)
                        value.validate(author)
                        require(value.work_id == item.work_id, "PAGE_IDENTITY_INVALID")
                        item = value
                    except LurkerError as error:
                        # Identity conflicts reject the whole page; missing optional metadata does not.
                        if error.code in {
                            "PAGE_IDENTITY_INVALID",
                            "DB_ERROR",
                            "OUTPUT_CLOSED",
                            "INTERRUPTED",
                        }:
                            raise
                        if error.global_stop:
                            global_error = error
                enriched.append(item)
            discovered = self.db.upsert_page(author, enriched)
            self.stats["pages_committed"] += 1
            self.stats["new_works"] += len(new_ids)
            for item in enriched:
                if item.work_id in new_ids:
                    work = asdict(item)
                    work.update(
                        author_name=author.name,
                        discovered_at=discovered,
                        missing_fields=[k for k in ("title", "published_at", "url") if work[k] is None],
                    )
                    self.output.record("work", work=work)
            if global_error:
                raise global_error
            if not all_new or not page.has_more:
                return
            cursor = page.next_cursor
