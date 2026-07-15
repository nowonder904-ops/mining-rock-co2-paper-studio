#!/usr/bin/env python3
"""Audit claim, citation, evidence, and placeholder closure in a manuscript."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from literature_register import normalize_doi
from workflow_common import emit, resolve_vault


CLAIM_RE = re.compile(r"<!--\s*CLAIM:\s*([A-Za-z0-9_.:-]+)\s*-->")
CITE_RE = re.compile(r"\[@(P-[A-Z0-9]+)\]|\[\[CITE:(P-[A-Z0-9]+)\]\]")
PLACEHOLDER_RE = re.compile(r"\[待填写\]|\[CITATION NEEDED\]|\[NEEDS USER DATA\]|\{\{[^{}]+\}\}|\bTODO\b", re.IGNORECASE)
PROCESS_LEAK_RE = re.compile(r"(?i)(as an ai|the user asked|审稿人要求我们|写作流程|internal note|chain of thought|prompt says)")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file(): return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description="审计正文中的主张—证据—引文闭环。")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--manuscript", default="07_Manuscript/Manuscript_Draft.md")
    parser.add_argument("--stage", choices=["S10", "S12", "S13"], default="S12")
    parser.add_argument("--output-json", default="08_Quality/Claim_Evidence_Audit.json")
    parser.add_argument("--output-md", default="08_Quality/Claim_Evidence_Audit.md")
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        manuscript = Path(args.manuscript)
        manuscript = manuscript.resolve() if manuscript.is_absolute() else (vault / manuscript).resolve()
        if not manuscript.is_file(): raise ValueError(f"正文不存在：{manuscript}")
        text = manuscript.read_text(encoding="utf-8")
        register = read_csv(vault / "03_Literature" / "literature_register.csv")
        claim_map = read_csv(vault / "05_Knowledge" / "Evidence" / "Claim_Citation_Map.csv")
        evidence = read_csv(vault / "05_Knowledge" / "Evidence" / "Evidence_Ledger.csv")
        by_paper = {row.get("paper_id", ""): row for row in register}
        claim_support: dict[str, list[dict[str, str]]] = {}
        for row in claim_map: claim_support.setdefault(row.get("claim_id", ""), []).append(row)
        claims = CLAIM_RE.findall(text)
        citations = [a or b for a, b in CITE_RE.findall(text)]
        findings = []
        if len(claims) != len(set(claims)):
            findings.append({"severity": "P1", "type": "duplicate-claim-id", "detail": "正文存在重复 CLAIM ID。"})
        for claim_id in sorted(set(claims)):
            support = [row for row in claim_support.get(claim_id, []) if row.get("status") == "verified" and row.get("support_grade") != "metadata-only" and row.get("source_locator")]
            if not support:
                findings.append({"severity": "P1", "type": "unsupported-claim", "claim_id": claim_id, "detail": "没有带原文定位的 verified 证据。"})
        for paper_id in sorted(set(citations)):
            row = by_paper.get(paper_id)
            if row is None:
                findings.append({"severity": "P0", "type": "unknown-citation", "paper_id": paper_id, "detail": "引文键不在文献登记表。"})
            elif row.get("fulltext_status") != "pdf-verified" or row.get("evidence_status") != "verified":
                findings.append({"severity": "P1", "type": "unverified-citation", "paper_id": paper_id, "detail": f"fulltext={row.get('fulltext_status')}; evidence={row.get('evidence_status')}"})
        used_evidence_ids = {row.get("evidence_id") for rows in claim_support.values() for row in rows if row.get("claim_id") in claims}
        evidence_ids = {row.get("evidence_id") for row in evidence}
        for evidence_id in sorted(x for x in used_evidence_ids if x and x not in evidence_ids):
            findings.append({"severity": "P0", "type": "orphan-evidence-reference", "evidence_id": evidence_id, "detail": "Claim map 指向不存在的证据账本条目。"})
        doi_seen: dict[str, str] = {}
        for row in register:
            doi = normalize_doi(row.get("doi", ""))
            if doi and doi in doi_seen and doi_seen[doi] != row.get("paper_id"):
                findings.append({"severity": "P1", "type": "duplicate-doi", "doi": doi, "paper_ids": [doi_seen[doi], row.get("paper_id")]})
            elif doi: doi_seen[doi] = row.get("paper_id", "")
        placeholders = [{"token": match.group(0), "offset": match.start()} for match in PLACEHOLDER_RE.finditer(text)]
        if placeholders and args.stage in {"S12", "S13"}:
            findings.append({"severity": "P1", "type": "unresolved-placeholders", "count": len(placeholders), "examples": placeholders[:20]})
        leaks = [{"token": match.group(0), "offset": match.start()} for match in PROCESS_LEAK_RE.finditer(text)]
        if leaks: findings.append({"severity": "P1", "type": "internal-process-language", "count": len(leaks), "examples": leaks[:20]})
        if re.search(r"(?<!\])\[(?:\d+|\d+[–-]\d+)\]", text) and not citations:
            findings.append({"severity": "P2", "type": "manual-numeric-citation", "detail": "检测到手写数字引文但没有内部稳定引文键，无法确定性追溯。"})
        blocking = [item for item in findings if item["severity"] in {"P0", "P1"}]
        report = {
            "status": "valid" if not blocking else "invalid", "stage": args.stage, "manuscript": str(manuscript),
            "claim_count": len(claims), "citation_count": len(citations), "placeholder_count": len(placeholders),
            "findings": findings, "note": "脚本只验证结构闭环；主张语义是否被证据充分支持仍需模型逐条读取来源后审阅。",
        }
        output_json = vault / args.output_json; output_md = vault / args.output_md
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# 主张—证据审计", "", f"- 状态：`{report['status']}`", f"- 主张：{len(claims)}", f"- 引文：{len(citations)}", f"- 未解决占位：{len(placeholders)}", "", "## 问题"]
        lines.extend(f"- **{x['severity']} / {x['type']}**：{x.get('detail', json.dumps(x, ensure_ascii=False))}" for x in findings)
        if not findings: lines.append("- 未发现结构性问题。")
        output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        emit({"status": report["status"], "blocking": len(blocking), "findings": len(findings), "report_json": str(output_json), "report_md": str(output_md)})
        return 0 if not blocking else 1
    except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
