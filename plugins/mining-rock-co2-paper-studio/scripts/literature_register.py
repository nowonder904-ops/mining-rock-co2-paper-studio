#!/usr/bin/env python3
"""Maintain the paper vault's single, deduplicated literature register."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from workflow_common import atomic_csv_write, emit, resolve_vault


FIELDS = [
    "paper_id", "title", "year", "doi", "journal", "authors", "domain", "source_type",
    "source_url", "oa_url", "abstract", "cited_by", "source_provenance", "search_batch",
    "screening_status", "fulltext_status", "pdf_path", "download_error", "content_hash",
    "identity_status", "identity_evidence", "identity_verified_at",
    "version_relation", "suggested_grade", "confirmed_grade", "reading_status", "evidence_status",
    "reading_note_path", "citation_card_path", "index_path", "topics", "last_transition_at",
    "blocker", "added_at", "notes",
]
FULLTEXT_STATUSES = [
    "unknown", "link-only", "pdf-available", "pdf-verified", "download-failed",
    "manual-download-required", "paywalled", "login-required", "captcha",
]
UPDATEABLE = set(FIELDS) - {"paper_id", "added_at"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return re.sub(r"^doi:\s*", "", value)


def normalize_title(value: str) -> str:
    return re.sub(r"\W+", "", value or "", flags=re.UNICODE).casefold()


def make_id(title: str, year: str, doi: str) -> str:
    basis = normalize_doi(doi) or f"{normalize_title(title)}|{(year or '').strip()}"
    return "P-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10].upper()


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{field: row.get(field, "") for field in FIELDS} for row in csv.DictReader(handle)]


def save_rows(path: Path, rows: list[dict[str, str]]) -> None:
    atomic_csv_write(path, FIELDS, [{field: row.get(field, "") for field in FIELDS} for row in rows])


def find_duplicate(rows: list[dict[str, str]], title: str, year: str, doi: str) -> dict[str, str] | None:
    doi_key = normalize_doi(doi)
    title_key = normalize_title(title)
    for row in rows:
        if doi_key and normalize_doi(row.get("doi", "")) == doi_key:
            return row
        if title_key and normalize_title(row.get("title", "")) == title_key:
            row_year = row.get("year", "").strip()
            if not year or not row_year or row_year == year.strip():
                return row
    return None


def merge_provenance(old: str, new: str) -> str:
    values = []
    for item in [*(old or "").split(";"), *(new or "").split(";")]:
        item = item.strip()
        if item and item not in values:
            values.append(item)
    return ";".join(values)


def upsert_record(rows: list[dict[str, str]], record: dict[str, str]) -> tuple[str, dict[str, str]]:
    title = (record.get("title") or "").strip()
    if not title:
        raise ValueError("文献题名不能为空。")
    year = (record.get("year") or "").strip()
    doi = normalize_doi(record.get("doi", ""))
    duplicate = find_duplicate(rows, title, year, doi)
    if duplicate:
        for field in FIELDS:
            value = str(record.get(field, "") or "").strip()
            if not value:
                continue
            if field == "source_provenance":
                duplicate[field] = merge_provenance(duplicate.get(field, ""), value)
            elif not duplicate.get(field, "").strip() or field in {"oa_url", "source_url", "cited_by", "abstract", "search_batch"}:
                duplicate[field] = value
        duplicate["doi"] = duplicate.get("doi", "") or doi
        duplicate["last_transition_at"] = now_iso()
        return "merged", duplicate

    row = {field: "" for field in FIELDS}
    row.update({field: str(record.get(field, "") or "").strip() for field in FIELDS})
    row["paper_id"] = row["paper_id"] or make_id(title, year, doi)
    row["title"] = title
    row["doi"] = doi
    row["domain"] = row["domain"] or "cross-domain"
    row["source_type"] = row["source_type"] or "journal"
    row["screening_status"] = row["screening_status"] or "candidate"
    row["fulltext_status"] = row["fulltext_status"] or "unknown"
    row["reading_status"] = row["reading_status"] or "not-started"
    row["evidence_status"] = row["evidence_status"] or "not-started"
    row["added_at"] = row["added_at"] or now_iso()
    row["last_transition_at"] = now_iso()
    rows.append(row)
    return "added", row


def register_path(vault: Path) -> Path:
    path = vault / "03_Literature" / "literature_register.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def add(args) -> int:
    vault = resolve_vault(args.vault)
    path = register_path(vault)
    rows = load_rows(path)
    record = {
        "title": args.title, "year": args.year, "doi": args.doi, "journal": args.journal,
        "authors": args.authors, "domain": args.domain, "source_type": args.source_type,
        "source_url": args.source_url, "oa_url": args.oa_url, "abstract": args.abstract,
        "source_provenance": args.source_provenance, "search_batch": args.search_batch,
        "screening_status": args.screening_status, "fulltext_status": args.fulltext_status,
        "download_error": args.download_error, "topics": args.topics, "notes": args.notes,
    }
    status, row = upsert_record(rows, record)
    save_rows(path, rows)
    emit({"status": status, "paper_id": row["paper_id"], "register": str(path)})
    return 0


def import_candidates(args) -> int:
    vault = resolve_vault(args.vault)
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"候选表不存在：{source}")
    path = register_path(vault)
    rows = load_rows(path)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        incoming = list(csv.DictReader(handle))
    counts = {"added": 0, "merged": 0}
    paper_ids = []
    for raw in incoming:
        if not (raw.get("title") or "").strip():
            continue
        status, row = upsert_record(rows, raw)
        counts[status] += 1
        paper_ids.append(row["paper_id"])
    save_rows(path, rows)
    emit({"status": "imported", **counts, "paper_ids": paper_ids, "register": str(path)})
    return 0


def list_rows_command(args) -> int:
    vault = resolve_vault(args.vault)
    rows = load_rows(register_path(vault))
    emit({"status": "ok", "count": len(rows), "rows": rows})
    return 0


def set_fulltext(args) -> int:
    vault = resolve_vault(args.vault)
    path = register_path(vault)
    rows = load_rows(path)
    matched = next((row for row in rows if row.get("paper_id", "").strip() == args.paper_id.strip()), None)
    if matched is None:
        raise ValueError(f"找不到 paper_id：{args.paper_id}")
    matched["fulltext_status"] = args.status
    for field in ["pdf_path", "download_error", "source_url", "content_hash", "blocker"]:
        value = getattr(args, field, None)
        if value is not None:
            matched[field] = value.strip()
    matched["last_transition_at"] = now_iso()
    save_rows(path, rows)
    emit({"status": "updated", "paper_id": args.paper_id, "fulltext_status": args.status, "register": str(path)})
    return 0


def update(args) -> int:
    vault = resolve_vault(args.vault)
    path = register_path(vault)
    rows = load_rows(path)
    matched = next((row for row in rows if row.get("paper_id") == args.paper_id), None)
    if matched is None:
        raise ValueError(f"找不到 paper_id：{args.paper_id}")
    changed = {}
    for assignment in args.set:
        if "=" not in assignment:
            raise ValueError(f"--set 必须使用 field=value：{assignment}")
        field, value = assignment.split("=", 1)
        field = field.strip()
        if field not in UPDATEABLE:
            raise ValueError(f"不允许更新字段：{field}")
        matched[field] = value.strip()
        changed[field] = value.strip()
    matched["last_transition_at"] = now_iso()
    save_rows(path, rows)
    emit({"status": "updated", "paper_id": args.paper_id, "changed": changed})
    return 0


def verify_identity(args) -> int:
    from download_failure_gate import resolve_pdf_path, valid_pdf

    vault = resolve_vault(args.vault)
    path = register_path(vault)
    rows = load_rows(path)
    matched = next((row for row in rows if row.get("paper_id") == args.paper_id), None)
    if matched is None:
        raise ValueError(f"找不到 paper_id：{args.paper_id}")
    if len(args.evidence.strip()) < 16:
        raise ValueError("身份核对证据过短；请记录题名、DOI或替代出版信息的实际核对依据。")
    if not matched.get("pdf_path", "").strip():
        raise ValueError("文献登记表缺少 pdf_path。")
    pdf_path = resolve_pdf_path(vault, matched["pdf_path"])
    ok, detail = valid_pdf(pdf_path)
    if not ok:
        raise ValueError(f"PDF 结构无效：{detail}")
    matched.update({
        "identity_status": "matched", "identity_evidence": args.evidence.strip(),
        "identity_verified_at": now_iso(), "fulltext_status": "pdf-verified",
        "download_error": "", "blocker": "", "last_transition_at": now_iso(),
    })
    save_rows(path, rows)
    emit({"status": "pdf-verified", "paper_id": args.paper_id, "pdf_path": str(pdf_path), "identity_evidence": args.evidence.strip()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="维护去重、可恢复的论文文献登记表。")
    sub = parser.add_subparsers(dest="command", required=True)
    add_parser = sub.add_parser("add")
    add_parser.add_argument("--vault", required=True)
    add_parser.add_argument("--title", required=True)
    for name in ["year", "doi", "journal", "authors", "source-url", "oa-url", "abstract", "source-provenance", "search-batch", "download-error", "topics", "notes"]:
        add_parser.add_argument(f"--{name}", default="")
    add_parser.add_argument("--domain", choices=["mining-engineering", "rock-mechanics", "co2-storage", "cross-domain"], default="cross-domain")
    add_parser.add_argument("--source-type", choices=["journal", "conference", "preprint", "thesis", "report", "web-lead"], default="journal")
    add_parser.add_argument("--screening-status", choices=["candidate", "priority", "include", "exclude"], default="candidate")
    add_parser.add_argument("--fulltext-status", choices=FULLTEXT_STATUSES, default="unknown")
    add_parser.set_defaults(func=add)

    import_parser = sub.add_parser("import-candidates")
    import_parser.add_argument("--vault", required=True)
    import_parser.add_argument("--input", required=True)
    import_parser.set_defaults(func=import_candidates)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--vault", required=True)
    list_parser.set_defaults(func=list_rows_command)

    set_parser = sub.add_parser("set-fulltext")
    set_parser.add_argument("--vault", required=True)
    set_parser.add_argument("--paper-id", required=True)
    set_parser.add_argument("--status", choices=FULLTEXT_STATUSES, required=True)
    for name in ["pdf-path", "download-error", "source-url", "content-hash", "blocker"]:
        set_parser.add_argument(f"--{name}")
    set_parser.set_defaults(func=set_fulltext)

    update_parser = sub.add_parser("update")
    update_parser.add_argument("--vault", required=True)
    update_parser.add_argument("--paper-id", required=True)
    update_parser.add_argument("--set", action="append", required=True, help="field=value，可重复")
    update_parser.set_defaults(func=update)

    identity_parser = sub.add_parser("verify-identity")
    identity_parser.add_argument("--vault", required=True)
    identity_parser.add_argument("--paper-id", required=True)
    identity_parser.add_argument("--evidence", required=True)
    identity_parser.set_defaults(func=verify_identity)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, csv.Error) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
