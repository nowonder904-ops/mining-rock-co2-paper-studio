#!/usr/bin/env python3
"""Prepare, ingest, and validate Mode B evidence assets in an Obsidian vault."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from literature_register import load_rows, save_rows
from workflow_common import atomic_csv_write, emit, resolve_vault


EVIDENCE_FIELDS = [
    "evidence_id", "claim_id", "claim_text", "claim_type", "paper_id", "source_locator",
    "evidence_type", "object", "scale", "conditions", "direction", "magnitude_or_range",
    "support_grade", "limitations", "counterevidence", "figure_table_ids",
    "manuscript_locations", "status", "created_at",
]
CLAIM_MAP_FIELDS = ["claim_id", "claim_text", "paper_id", "evidence_id", "support_grade", "source_locator", "manuscript_locations", "status"]
SUPPORT_GRADES = {"strong", "partial", "background", "limiting", "metadata-only"}
CLAIM_TYPES = {"experiment", "numerical-simulation", "theory", "field-monitoring", "statistical-association", "literature-synthesis", "author-inference"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", value.strip(), flags=re.UNICODE).strip("-")
    return cleaned[:80] or "topic"


def csv_rows(path: Path, fields: list[str]) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0: return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{field: row.get(field, "") for field in fields} for row in csv.DictReader(handle)]


def replace_auto_block(path: Path, key: str, content: str, header: str = "") -> None:
    begin = f"<!-- AUTO:{key}:BEGIN -->"
    end = f"<!-- AUTO:{key}:END -->"
    block = f"{begin}\n{content.rstrip()}\n{end}"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), flags=re.DOTALL)
        text = pattern.sub(block, text) if pattern.search(text) else text.rstrip() + "\n\n" + block + "\n"
    else:
        text = header.rstrip() + "\n\n" + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def decision_rows(vault: Path) -> list[dict[str, str]]:
    path = vault / "00_Control" / "mode_b_decisions.csv"
    if not path.is_file(): raise ValueError("缺少 Mode B 用户确认表。")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def note_header(row: dict[str, str], grade: str) -> str:
    topics = [x.strip() for x in row.get("topics", "").split(";") if x.strip()]
    topic_yaml = "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in topics) + "]"
    return f'''---
paper_id: "{row.get('paper_id','')}"
title: {json.dumps(row.get('title',''), ensure_ascii=False)}
year: "{row.get('year','')}"
doi: "{row.get('doi','')}"
source_type: "{row.get('source_type','')}"
domain: "{row.get('domain','')}"
topics: {topic_yaml}
fulltext_status: "{row.get('fulltext_status','')}"
mode_b_grade: "{grade}"
reading_status: "prepared"
evidence_status: "pending"
pdf_path: {json.dumps(row.get('pdf_path',''), ensure_ascii=False)}
created: "{now_iso()}"
updated: "{now_iso()}"
---

# {row.get('title','')}

> [!warning] 证据边界
> 只有带 PDF 页码、章节、公式、图或表定位的内容才可进入可引用证据；摘要和元数据只能作为线索。
'''


def prepare(args, vault: Path) -> int:
    register_path = vault / "03_Literature" / "literature_register.csv"
    register_rows = load_rows(register_path)
    register = {row.get("paper_id", ""): row for row in register_rows}
    decisions = sorted(decision_rows(vault), key=lambda row: int(row.get("order") or 999999))
    created, preserved, errors = [], [], []
    for decision in decisions:
        grade = decision.get("confirmed_grade", "").upper()
        if grade not in {"A", "B", "C"}: continue
        paper_id = decision.get("paper_id", "")
        row = register.get(paper_id)
        if not row: errors.append(f"登记表缺少 {paper_id}"); continue
        if row.get("fulltext_status") != "pdf-verified": errors.append(f"{paper_id} 未达到 pdf-verified"); continue
        note = vault / "04_Reading" / "Notes" / f"{paper_id}.md"
        card = vault / "04_Reading" / "Citation_Cards" / f"{paper_id}.md"
        if note.exists(): preserved.append(str(note.relative_to(vault)))
        else:
            note.parent.mkdir(parents=True, exist_ok=True); note.write_text(note_header(row, grade) + "\n## 精读内容\n\n[待模型基于全文生成]\n", encoding="utf-8", newline="\n"); created.append(str(note.relative_to(vault)))
        if card.exists(): preserved.append(str(card.relative_to(vault)))
        else:
            card.parent.mkdir(parents=True, exist_ok=True); card.write_text(f"---\npaper_id: \"{paper_id}\"\ntype: citation-card\nstatus: prepared\n---\n\n# 引用卡：{row.get('title','')}\n\n[待模型基于全文证据生成]\n", encoding="utf-8", newline="\n"); created.append(str(card.relative_to(vault)))
        row["reading_note_path"] = str(note.relative_to(vault))
        row["citation_card_path"] = str(card.relative_to(vault))
        row["reading_status"] = "prepared"
        row["last_transition_at"] = now_iso()
    save_rows(register_path, register_rows)
    index = vault / "04_Reading" / "Citation_Cards" / "Citation_Cards.md"
    links = [f"- [[{d.get('paper_id')}|{register.get(d.get('paper_id'),{}).get('title',d.get('paper_id'))}]]" for d in decisions if d.get("confirmed_grade") in {"A", "B", "C"}]
    replace_auto_block(index, "citation-card-index", "# 引用卡索引\n\n" + "\n".join(links), "")
    emit({"status": "prepared" if not errors else "blocked_by_evidence", "created": created, "preserved": preserved, "errors": errors})
    return 0 if not errors else 1


def validate_record(record: dict[str, Any], expected_paper_id: str) -> list[str]:
    errors = []
    if record.get("paper_id") != expected_paper_id: errors.append("record.paper_id 与命令参数不一致。")
    for field in ["summary", "research_question", "methods", "results", "limitations", "topics"]:
        if field not in record or record[field] is None or record[field] == "" or record[field] == []:
            errors.append(f"缺少或为空：{field}")
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence: errors.append("evidence 必须是非空列表。")
    else:
        ids = set()
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, dict): errors.append(f"evidence[{index}] 不是对象。"); continue
            evidence_id = str(item.get("evidence_id", "")).strip()
            if not evidence_id: errors.append(f"evidence[{index}] 缺 evidence_id。")
            elif evidence_id in ids: errors.append(f"evidence_id 重复：{evidence_id}")
            ids.add(evidence_id)
            for field in ["claim_id", "claim_text", "claim_type", "source_locator", "evidence_type", "object", "scale", "conditions", "support_grade", "limitations"]:
                if not str(item.get(field, "")).strip(): errors.append(f"{evidence_id or index} 缺 {field}。")
            if item.get("support_grade") not in SUPPORT_GRADES: errors.append(f"{evidence_id or index} support_grade 无效。")
            if item.get("claim_type") not in CLAIM_TYPES: errors.append(f"{evidence_id or index} claim_type 无效。")
            if item.get("support_grade") != "metadata-only" and not re.search(r"(?i)(p(?:age)?\.?\s*\d+|页\s*\d+|fig(?:ure)?\.?\s*\w+|图\s*\w+|table\s*\w+|表\s*\w+|section\s*[\d.]+|§\s*[\d.]+|eq(?:uation)?\.?\s*\w+|公式\s*\w+)", str(item.get("source_locator", ""))):
                errors.append(f"{evidence_id or index} source_locator 缺页码/章节/图表/公式锚点。")
    return errors


def markdown_list(value: Any) -> str:
    if isinstance(value, dict): return "\n".join(f"- **{key}**：{val}" for key, val in value.items())
    if isinstance(value, list): return "\n".join(f"- {item if not isinstance(item, dict) else json.dumps(item, ensure_ascii=False)}" for item in value)
    return str(value)


def upsert_canvas(vault: Path, paper_id: str, title: str, evidence: list[dict[str, Any]]) -> None:
    path = vault / "90_Maps" / "Mechanism_Evidence_Map.canvas"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"nodes": [], "edges": []}
    nodes = [node for node in data.get("nodes", []) if not str(node.get("id", "")).startswith(f"auto-{paper_id}-")]
    edges = [edge for edge in data.get("edges", []) if not str(edge.get("id", "")).startswith(f"auto-{paper_id}-")]
    paper_node = f"auto-{paper_id}-paper"
    nodes.append({"id": paper_node, "type": "file", "file": f"04_Reading/Notes/{paper_id}.md", "x": 0, "y": len(nodes) * 80, "width": 360, "height": 100})
    for idx, item in enumerate(evidence):
        claim_node = f"auto-{paper_id}-{slug(str(item.get('claim_id','claim')))}"
        nodes.append({"id": claim_node, "type": "text", "text": f"{item.get('claim_id')}: {item.get('claim_text')}", "x": 480, "y": idx * 140, "width": 420, "height": 120})
        edges.append({"id": f"auto-{paper_id}-edge-{idx}", "fromNode": paper_node, "toNode": claim_node, "fromSide": "right", "toSide": "left", "label": item.get("support_grade", "")})
    path.write_text(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ingest(args, vault: Path) -> int:
    record_path = Path(args.record).expanduser().resolve()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    errors = validate_record(record, args.paper_id)
    register_path = vault / "03_Literature" / "literature_register.csv"
    register_rows = load_rows(register_path)
    row = next((x for x in register_rows if x.get("paper_id") == args.paper_id), None)
    if row is None: errors.append("paper_id 不在文献登记表。")
    if row and row.get("fulltext_status") != "pdf-verified": errors.append("全文尚未通过 pdf-verified。")
    if errors:
        emit({"status": "blocked_by_evidence", "paper_id": args.paper_id, "errors": errors})
        return 1

    evidence = record["evidence"]
    sections = [
        "## 摘要\n\n" + markdown_list(record["summary"]),
        "## 研究问题\n\n" + markdown_list(record["research_question"]),
        "## 方法与边界条件\n\n" + markdown_list(record["methods"]),
        "## 结果\n\n" + markdown_list(record["results"]),
        "## 图表与公式\n\n" + markdown_list(record.get("figures_tables", ["未要求或不适用；见 Mode B 配置。"])),
        "## 局限、反例与可迁移边界\n\n" + markdown_list(record["limitations"]),
        "## 可引用证据\n\n" + "\n\n".join(
            f"### {item['evidence_id']} / {item['claim_id']}\n\n- 主张：{item['claim_text']}\n- 类型：{item['claim_type']} / {item['evidence_type']}\n- 原文定位：{item['source_locator']}\n- 对象—尺度—条件：{item['object']}；{item['scale']}；{item['conditions']}\n- 方向与效应：{item.get('direction','')}；{item.get('magnitude_or_range','')}\n- 支撑等级：{item['support_grade']}\n- 局限：{item['limitations']}\n- 反证：{item.get('counterevidence','')}"
            for item in evidence
        ),
        "## 主题链接\n\n" + " · ".join(f"[[{slug(str(topic))}]]" for topic in record["topics"]),
    ]
    note = vault / "04_Reading" / "Notes" / f"{args.paper_id}.md"
    replace_auto_block(note, f"reading-{args.paper_id}", "\n\n".join(sections), note_header(row, row.get("confirmed_grade", "")))
    card = vault / "04_Reading" / "Citation_Cards" / f"{args.paper_id}.md"
    card_content = "## 一句话用途\n\n" + str(record.get("citation_use", record["summary"])) + "\n\n## 可引用条目\n\n" + "\n".join(f"- **{x['claim_id']}**：{x['claim_text']}（{x['source_locator']}；{x['support_grade']}）" for x in evidence if x["support_grade"] != "metadata-only")
    replace_auto_block(card, f"citation-{args.paper_id}", card_content, f"---\npaper_id: \"{args.paper_id}\"\ntype: citation-card\nstatus: verified\n---\n\n# 引用卡：{row.get('title','')}")

    ledger_path = vault / "05_Knowledge" / "Evidence" / "Evidence_Ledger.csv"
    ledger = [x for x in csv_rows(ledger_path, EVIDENCE_FIELDS) if x.get("paper_id") != args.paper_id]
    for item in evidence:
        entry = {field: "" for field in EVIDENCE_FIELDS}
        entry.update({field: str(item.get(field, "") or "") for field in EVIDENCE_FIELDS})
        entry.update({"paper_id": args.paper_id, "status": "gap" if item["support_grade"] == "metadata-only" else "verified", "created_at": now_iso()})
        ledger.append(entry)
    atomic_csv_write(ledger_path, EVIDENCE_FIELDS, ledger)
    map_path = vault / "05_Knowledge" / "Evidence" / "Claim_Citation_Map.csv"
    claim_rows = [x for x in csv_rows(map_path, CLAIM_MAP_FIELDS) if x.get("paper_id") != args.paper_id]
    for item in evidence:
        claim_rows.append({"claim_id": item["claim_id"], "claim_text": item["claim_text"], "paper_id": args.paper_id, "evidence_id": item["evidence_id"], "support_grade": item["support_grade"], "source_locator": item["source_locator"], "manuscript_locations": str(item.get("manuscript_locations", "")), "status": "gap" if item["support_grade"] == "metadata-only" else "verified"})
    atomic_csv_write(map_path, CLAIM_MAP_FIELDS, claim_rows)

    wiki_index = vault / "05_Knowledge" / "Wiki" / "Index.md"
    wiki_links = []
    for topic in record["topics"]:
        topic_name = slug(str(topic)); wiki = vault / "05_Knowledge" / "Wiki" / f"{topic_name}.md"
        replace_auto_block(wiki, f"paper-{args.paper_id}", f"- [[../../04_Reading/Notes/{args.paper_id}|{row.get('title','')}]]：{record['summary']}", f"---\ntype: topic-wiki\ntopic: {json.dumps(str(topic), ensure_ascii=False)}\n---\n\n# {topic}")
        wiki_links.append(f"- [[{topic_name}]]")
    replace_auto_block(wiki_index, "topic-index", "\n".join(sorted(set(wiki_links))), "# 主题 Wiki 索引")
    upsert_canvas(vault, args.paper_id, row.get("title", ""), evidence)
    row.update({"reading_status": "completed", "evidence_status": "verified", "reading_note_path": str(note.relative_to(vault)), "citation_card_path": str(card.relative_to(vault)), "index_path": str(wiki_index.relative_to(vault)), "last_transition_at": now_iso(), "blocker": ""})
    save_rows(register_path, register_rows)
    emit({"status": "ingested", "paper_id": args.paper_id, "note": str(note), "citation_card": str(card), "evidence_count": len(evidence), "topics": record["topics"]})
    return 0


def validate(args, vault: Path) -> int:
    errors = []
    register_rows = load_rows(vault / "03_Literature" / "literature_register.csv")
    register = {row.get("paper_id", ""): row for row in register_rows}
    decisions = decision_rows(vault)
    selected = {row.get("paper_id") for row in decisions if row.get("confirmed_grade") in {"A", "B", "C"}}
    ledger = csv_rows(vault / "05_Knowledge" / "Evidence" / "Evidence_Ledger.csv", EVIDENCE_FIELDS)
    for paper_id in sorted(selected):
        row = register.get(paper_id, {})
        for field in ["reading_note_path", "citation_card_path"]:
            value = row.get(field, "")
            if not value or not (vault / value).is_file(): errors.append(f"{paper_id} 缺 {field} 或文件不存在。")
        evidence = [x for x in ledger if x.get("paper_id") == paper_id]
        if not evidence: errors.append(f"{paper_id} 没有证据账本条目。")
        if not any(x.get("status") == "verified" and x.get("source_locator") for x in evidence): errors.append(f"{paper_id} 没有带原文定位的 verified 证据。")
    for item in ledger:
        if item.get("support_grade") not in SUPPORT_GRADES: errors.append(f"{item.get('evidence_id')} 支撑等级无效。")
        if item.get("status") == "verified" and not item.get("source_locator"): errors.append(f"{item.get('evidence_id')} 缺 source_locator。")
    for rel in ["04_Reading/Citation_Cards/Citation_Cards.md", "05_Knowledge/Wiki/Index.md", "90_Maps/Mechanism_Evidence_Map.canvas"]:
        if not (vault / rel).is_file(): errors.append(f"缺少 {rel}")
    emit({"status": "valid" if not errors else "invalid", "selected_papers": len(selected), "evidence_rows": len(ledger), "errors": errors})
    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="准备、写入并验证 Mode B 证据知识库。")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_p = sub.add_parser("prepare"); prepare_p.add_argument("--vault", required=True); prepare_p.set_defaults(func=prepare)
    ingest_p = sub.add_parser("ingest"); ingest_p.add_argument("--vault", required=True); ingest_p.add_argument("--paper-id", required=True); ingest_p.add_argument("--record", required=True); ingest_p.set_defaults(func=ingest)
    validate_p = sub.add_parser("validate"); validate_p.add_argument("--vault", required=True); validate_p.set_defaults(func=validate)
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        return args.func(args, vault)
    except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
