#!/usr/bin/env python3
"""Search scholarly APIs and emit one deduplicated, provenance-rich candidate table."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from literature_register import FIELDS, make_id, normalize_doi, normalize_title
from workflow_common import atomic_csv_write, emit, resolve_vault


USER_AGENT = "mining-rock-co2-paper-studio/0.2 (standalone scholarly search)"
CANDIDATE_FIELDS = FIELDS


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def first(value: Any, default: str = "") -> str:
    if isinstance(value, list):
        return str(value[0]) if value else default
    return str(value) if value not in {None, ""} else default


def year_from_parts(value: Any) -> str:
    try:
        return str(value["date-parts"][0][0])
    except (KeyError, IndexError, TypeError):
        return ""


def inverted_abstract(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    words: list[tuple[int, str]] = []
    for token, positions in value.items():
        for position in positions or []:
            words.append((int(position), token))
    return " ".join(token for _, token in sorted(words))


def request_bytes(url: str, timeout: float, retries: int) -> bytes:
    last: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml;q=0.9, */*;q=0.1"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise
            retry_after = exc.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else min(8.0, 1.5 * (2 ** attempt))
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt >= retries:
                raise
            time.sleep(min(8.0, 1.5 * (2 ** attempt)))
    raise RuntimeError(str(last))


def json_response(url: str, timeout: float, retries: int) -> dict[str, Any]:
    return json.loads(request_bytes(url, timeout, retries).decode("utf-8", errors="replace"))


def base_record(**values: Any) -> dict[str, str]:
    record = {field: "" for field in CANDIDATE_FIELDS}
    record.update({key: str(value or "").strip() for key, value in values.items() if key in record})
    record["doi"] = normalize_doi(record["doi"])
    record["paper_id"] = record["paper_id"] or make_id(record["title"], record["year"], record["doi"])
    record["domain"] = record["domain"] or "cross-domain"
    record["source_type"] = record["source_type"] or "journal"
    record["screening_status"] = record["screening_status"] or "candidate"
    record["fulltext_status"] = record["fulltext_status"] or ("link-only" if record["oa_url"] else "unknown")
    record["reading_status"] = "not-started"
    record["evidence_status"] = "not-started"
    record["added_at"] = now_iso()
    record["last_transition_at"] = now_iso()
    return record


def crossref(query: str, from_year: int | None, to_year: int | None, limit: int, timeout: float, retries: int) -> tuple[list[dict[str, str]], Any]:
    params = {"query.bibliographic": query, "rows": str(limit), "select": "DOI,title,author,published,container-title,URL,type,is-referenced-by-count,link,abstract"}
    filters = []
    if from_year: filters.append(f"from-pub-date:{from_year}-01-01")
    if to_year: filters.append(f"until-pub-date:{to_year}-12-31")
    if filters: params["filter"] = ",".join(filters)
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    raw = json_response(url, timeout, retries)
    records = []
    for item in raw.get("message", {}).get("items", []):
        title = first(item.get("title"))
        if not title: continue
        authors = "; ".join(" ".join(filter(None, [a.get("given", ""), a.get("family", "")])).strip() for a in item.get("author", []))
        links = item.get("link") or []
        oa = next((x.get("URL", "") for x in links if "pdf" in (x.get("content-type", "").lower())), "")
        records.append(base_record(
            title=title, year=year_from_parts(item.get("published", {})), doi=item.get("DOI", ""),
            journal=first(item.get("container-title")), authors=authors, source_type="journal",
            source_url=item.get("URL", ""), oa_url=oa, abstract=re.sub(r"<[^>]+>", " ", item.get("abstract", "")),
            cited_by=item.get("is-referenced-by-count", 0), source_provenance="crossref",
        ))
    return records, raw


def openalex(query: str, from_year: int | None, to_year: int | None, limit: int, timeout: float, retries: int) -> tuple[list[dict[str, str]], Any]:
    filters = []
    if from_year: filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year: filters.append(f"to_publication_date:{to_year}-12-31")
    params = {"search": query, "per-page": str(min(limit, 200)), "mailto": ""}
    if filters: params["filter"] = ",".join(filters)
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
    raw = json_response(url, timeout, retries)
    records = []
    for item in raw.get("results", []):
        title = item.get("display_name") or item.get("title") or ""
        if not title: continue
        primary = item.get("primary_location") or {}
        source = primary.get("source") or {}
        oa = item.get("best_oa_location") or {}
        authors = "; ".join((x.get("author") or {}).get("display_name", "") for x in item.get("authorships", []))
        doi = normalize_doi(item.get("doi") or "")
        records.append(base_record(
            title=title, year=item.get("publication_year", ""), doi=doi, journal=source.get("display_name", ""),
            authors=authors, source_type=(item.get("type") or "journal-article"),
            source_url=primary.get("landing_page_url", "") or item.get("id", ""),
            oa_url=oa.get("pdf_url", "") or oa.get("landing_page_url", ""),
            abstract=inverted_abstract(item.get("abstract_inverted_index")), cited_by=item.get("cited_by_count", 0),
            source_provenance="openalex",
        ))
    return records, raw


def europepmc(query: str, from_year: int | None, to_year: int | None, limit: int, timeout: float, retries: int) -> tuple[list[dict[str, str]], Any]:
    date_clause = ""
    if from_year or to_year:
        date_clause = f" AND FIRST_PDATE:[{from_year or 1000}-01-01 TO {to_year or 3000}-12-31]"
    params = {"query": query + date_clause, "format": "json", "pageSize": str(min(limit, 1000)), "resultType": "core"}
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)
    raw = json_response(url, timeout, retries)
    records = []
    for item in raw.get("resultList", {}).get("result", []):
        title = item.get("title", "")
        if not title: continue
        pmcid = item.get("pmcid", "")
        oa = f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""
        records.append(base_record(
            title=title, year=item.get("pubYear", ""), doi=item.get("doi", ""), journal=item.get("journalTitle", ""),
            authors=item.get("authorString", ""), source_type="journal", source_url=f"https://europepmc.org/article/{item.get('source','MED')}/{item.get('id','')}",
            oa_url=oa, abstract=item.get("abstractText", ""), cited_by=item.get("citedByCount", 0), source_provenance="europepmc",
        ))
    return records, raw


def arxiv(query: str, from_year: int | None, to_year: int | None, limit: int, timeout: float, retries: int) -> tuple[list[dict[str, str]], Any]:
    params = {"search_query": f"all:{query}", "start": "0", "max_results": str(min(limit, 200)), "sortBy": "submittedDate", "sortOrder": "descending"}
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    payload = request_bytes(url, timeout, retries)
    root = ET.fromstring(payload)
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    records = []
    for entry in root.findall("a:entry", ns):
        published = (entry.findtext("a:published", default="", namespaces=ns) or "")
        year = published[:4]
        if (from_year and year and int(year) < from_year) or (to_year and year and int(year) > to_year):
            continue
        identifier = entry.findtext("a:id", default="", namespaces=ns)
        pdf = next((link.get("href", "") for link in entry.findall("a:link", ns) if link.get("title") == "pdf"), "")
        records.append(base_record(
            title=" ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split()), year=year,
            journal="arXiv", authors="; ".join(x.findtext("a:name", default="", namespaces=ns) for x in entry.findall("a:author", ns)),
            source_type="preprint", source_url=identifier, oa_url=pdf,
            abstract=" ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split()), source_provenance="arxiv",
        ))
    return records, payload.decode("utf-8", errors="replace")


ADAPTERS: dict[str, Callable[..., tuple[list[dict[str, str]], Any]]] = {
    "crossref": crossref, "openalex": openalex, "europepmc": europepmc, "arxiv": arxiv,
}


def merge(records: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    by_key: dict[str, dict[str, str]] = {}
    for record in records:
        key = f"doi:{normalize_doi(record['doi'])}" if record.get("doi") else f"title:{normalize_title(record['title'])}|{record.get('year','')}"
        current = by_key.get(key)
        if current is None:
            by_key[key] = record
            merged.append(record)
            continue
        sources = [x for x in (current.get("source_provenance", "") + ";" + record.get("source_provenance", "")).split(";") if x]
        current["source_provenance"] = ";".join(dict.fromkeys(sources))
        for field in CANDIDATE_FIELDS:
            if not current.get(field) and record.get(field): current[field] = record[field]
        try:
            current["cited_by"] = str(max(int(current.get("cited_by") or 0), int(record.get("cited_by") or 0)))
        except ValueError:
            pass
    return merged


def export_bib(records: list[dict[str, str]], path: Path) -> None:
    blocks = []
    for row in records:
        key = re.sub(r"[^A-Za-z0-9_-]", "", row["paper_id"])
        fields = {"title": row["title"], "author": row["authors"].replace(";", " and"), "year": row["year"], "journal": row["journal"], "doi": row["doi"], "url": row["source_url"]}
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields.items() if value)
        blocks.append(f"@article{{{key},\n{body}\n}}")
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def export_ris(records: list[dict[str, str]], path: Path) -> None:
    lines = []
    for row in records:
        lines.extend(["TY  - JOUR", f"ID  - {row['paper_id']}", f"TI  - {row['title']}"])
        for author in filter(None, (x.strip() for x in row["authors"].split(";"))): lines.append(f"AU  - {author}")
        for tag, field in [("PY", "year"), ("JO", "journal"), ("DO", "doi"), ("UR", "source_url"), ("AB", "abstract")]:
            if row.get(field): lines.append(f"{tag}  - {row[field]}")
        lines.extend(["ER  - ", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def export_enw(records: list[dict[str, str]], path: Path) -> None:
    lines = []
    for row in records:
        lines.extend(["%0 Journal Article", f"%F {row['paper_id']}", f"%T {row['title']}"])
        for author in filter(None, (x.strip() for x in row["authors"].split(";"))): lines.append(f"%A {author}")
        for tag, field in [("%D", "year"), ("%J", "journal"), ("%R", "doi"), ("%U", "source_url"), ("%X", "abstract")]:
            if row.get(field): lines.append(f"{tag} {row[field]}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="多源检索近期与经典候选文献，并生成可复现记录。")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--query", action="append", required=True, help="检索式，可重复")
    parser.add_argument("--from-year", type=int, default=datetime.now().year - 1, help="近期窗口起始年；默认当前年及上一年")
    parser.add_argument("--to-year", type=int, default=datetime.now().year)
    parser.add_argument("--sources", default="crossref,openalex,europepmc,arxiv")
    parser.add_argument("--max-per-source", type=int, default=50)
    parser.add_argument("--include-classics", type=int, default=20, help="按引用次数提供的旧文献候选数；这是代理指标，不等同于经典认定")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--fixture-dir", help="离线测试夹具目录；文件名为 <source>.json 或 arxiv.xml")
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        selected = [x.strip().lower() for x in args.sources.split(",") if x.strip()]
        unknown = sorted(set(selected) - set(ADAPTERS))
        if unknown: raise ValueError(f"未知数据源：{', '.join(unknown)}")
        batch = "SEARCH-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        raw_dir = vault / "02_Search" / "runs" / batch
        raw_dir.mkdir(parents=True, exist_ok=False)
        all_records: list[dict[str, str]] = []
        source_reports = []
        for query in args.query:
            for source in selected:
                try:
                    if args.fixture_dir:
                        ext = "xml" if source == "arxiv" else "json"
                        payload = (Path(args.fixture_dir).expanduser().resolve() / f"{source}.{ext}").read_bytes()
                        if source == "arxiv":
                            original = request_bytes
                            globals()["request_bytes"] = lambda *a, _p=payload, **k: _p
                        else:
                            original = json_response
                            raw_fixture = json.loads(payload.decode("utf-8"))
                            globals()["json_response"] = lambda *a, _r=raw_fixture, **k: _r
                    records, raw = ADAPTERS[source](query, args.from_year, args.to_year, args.max_per_source, args.timeout, args.retries)
                    if args.fixture_dir:
                        globals()["request_bytes" if source == "arxiv" else "json_response"] = original
                    for row in records:
                        row["search_batch"] = batch
                        row["notes"] = f"query={query}; window={args.from_year or '*'}-{args.to_year or '*'}"
                    all_records.extend(records)
                    raw_path = raw_dir / f"{source}-{hashlib.sha1(query.encode('utf-8')).hexdigest()[:8]}.{'xml' if isinstance(raw, str) else 'json'}"
                    raw_path.write_text(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                    source_reports.append({"source": source, "query": query, "status": "ok", "count": len(records), "raw": str(raw_path)})
                except Exception as exc:
                    source_reports.append({"source": source, "query": query, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})

        # Recent-window search and classic-candidate search are separate runs.  "Classic"
        # is only a citation-count proxy until the user and model inspect the evidence.
        if args.include_classics > 0 and args.from_year and not args.fixture_dir:
            classic_to = args.from_year - 1
            for query in args.query:
                for source in [name for name in selected if name in {"crossref", "openalex"}]:
                    try:
                        classic_records, raw = ADAPTERS[source](query, None, classic_to, max(args.include_classics * 3, 30), args.timeout, args.retries)
                        classic_records.sort(key=lambda row: int(row.get("cited_by") or 0), reverse=True)
                        for row in classic_records[: args.include_classics]:
                            row["search_batch"] = batch
                            row["notes"] = f"query={query}; classic-candidate-by-citation-proxy; until={classic_to}"
                        all_records.extend(classic_records[: args.include_classics])
                        raw_path = raw_dir / f"{source}-classic-{hashlib.sha1(query.encode('utf-8')).hexdigest()[:8]}.json"
                        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                        source_reports.append({"source": source, "query": query, "status": "ok", "count": min(len(classic_records), args.include_classics), "mode": "classic-proxy", "raw": str(raw_path)})
                    except Exception as exc:
                        source_reports.append({"source": source, "query": query, "status": "failed", "mode": "classic-proxy", "error": f"{type(exc).__name__}: {exc}"})

        records = merge(all_records)
        for row in records:
            row["paper_id"] = make_id(row["title"], row["year"], row["doi"])
        output = vault / "02_Search" / "search_candidates.csv"
        atomic_csv_write(output, CANDIDATE_FIELDS, records)
        export_bib(records, vault / "02_Search" / "search_candidates.bib")
        export_ris(records, vault / "02_Search" / "search_candidates.ris")
        export_enw(records, vault / "02_Search" / "search_candidates.enw")
        log = vault / "02_Search" / "search_log.csv"
        log_fields = ["search_id", "run_at", "database", "query", "time_window", "filters", "result_count", "export_path", "status", "notes"]
        existing = []
        if log.exists() and log.stat().st_size:
            with log.open("r", encoding="utf-8-sig", newline="") as handle: existing = list(csv.DictReader(handle))
        for report in source_reports:
            existing.append({"search_id": batch, "run_at": now_iso(), "database": report["source"], "query": report["query"], "time_window": f"{args.from_year or '*'}-{args.to_year or '*'}", "filters": "", "result_count": str(report.get("count", 0)), "export_path": str(output), "status": report["status"], "notes": report.get("error", "")})
        atomic_csv_write(log, log_fields, existing)
        status = "ok" if all(x["status"] == "ok" for x in source_reports) else ("partial" if records else "external_unavailable")
        emit({"status": status, "batch": batch, "candidates": len(records), "candidate_table": str(output), "source_reports": source_reports, "next": f"python literature_register.py import-candidates --vault <vault> --input {output}"})
        return 0 if records else 1
    except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
