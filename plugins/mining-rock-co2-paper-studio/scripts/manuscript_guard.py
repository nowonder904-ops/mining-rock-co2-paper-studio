#!/usr/bin/env python3
"""Deterministic pre-writing, revision-invariant, and final-integrity checks."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from workflow_common import atomic_json_write, emit, load_json_yaml, resolve_vault


PLACEHOLDERS = (
    "[待填写]",
    "[待补充]",
    "{{",
    "AUTHOR_INPUT_NEEDED",
    "TBD",
    "TODO",
)
MANUSCRIPT_CANDIDATES = (
    "07_Manuscript/Manuscript_AuthorVoice.md",
    "07_Manuscript/Manuscript_Humanized.md",
    "07_Manuscript/Manuscript_Polished.md",
    "07_Manuscript/Manuscript_Draft.md",
)
NUMBER_RE = re.compile(
    r"(?<![\w.])[-+±]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"(?:\s*(?:%|‰|Pa|kPa|MPa|GPa|K|°C|℃|s|min|h|d|day|days|mm|cm|m|km|"
    r"μm|µm|nm|mD|D|m\^?2|m²|m\^?3|m³|kg(?:/m3|/m³)?|g(?:/cm3|/cm³)?|"
    r"mol(?:/L)?|Hz|kHz|MHz|N|kN|J|kJ|W|kW))?",
    re.IGNORECASE,
)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
FIGURE_REF_RE = re.compile(
    r"\b(?:fig(?:ure)?|table|eq(?:uation)?|supplementary\s+(?:fig(?:ure)?|table))\.?\s*[A-Z]?\d+[A-Z]?\b"
    r"|(?:图|表|式)\s*[A-Z]?\d+[A-Z]?",
    re.IGNORECASE,
)
CITATION_RE = re.compile(r"\[(?:\s*\d+\s*(?:[-–—,;]\s*\d+\s*)*)\]")
ID_RE = re.compile(r"\b[A-Z]{1,6}-\d{2,}\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def norm_token(value: str) -> str:
    return norm_space(value).replace("−", "-").replace("–", "-").replace("—", "-").lower()


def placeholder_hits(text: str) -> list[str]:
    lower = text.lower()
    return sorted(marker for marker in PLACEHOLDERS if marker.lower() in lower)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def find_field(fields: Iterable[str], choices: Iterable[str]) -> str | None:
    by_lower = {field.lower(): field for field in fields}
    return next((by_lower[item.lower()] for item in choices if item.lower() in by_lower), None)


def select_manuscript(vault: Path, explicit: str | None) -> Path:
    if explicit:
        raw = Path(explicit).expanduser()
        path = raw.resolve() if raw.is_absolute() else (vault / raw).resolve()
        if not raw.is_absolute() and not path.is_relative_to(vault):
            raise ValueError(f"manuscript_path_escapes_vault:{explicit}")
        if not path.is_file():
            raise ValueError(f"manuscript_not_found:{path}")
        return path
    for rel in MANUSCRIPT_CANDIDATES:
        path = vault / rel
        if path.is_file():
            return path
    raise ValueError("manuscript_not_found:no_default_candidate")


def write_or_emit(report: dict[str, Any], destination: Path, check_only: bool) -> int:
    if not check_only:
        atomic_json_write(destination, report)
    payload = dict(report)
    payload["written"] = None if check_only else str(destination)
    emit(payload)
    return 0 if report["status"] == "valid" else 1


def plan_audit(args: argparse.Namespace) -> int:
    vault = resolve_vault(args.vault)
    context_path = Path(args.context).expanduser().resolve() if args.context else vault / "07_Manuscript" / "manuscript_context.json"
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    if not context_path.is_file():
        errors.append(f"missing_context:{context_path}")
        context: dict[str, Any] = {}
    else:
        context = json.loads(context_path.read_text(encoding="utf-8"))
        validation = context.get("validation", {})
        if validation.get("status") != "ready":
            errors.append("context_not_ready")
        context_errors = validation.get("errors", [])
        if context_errors:
            errors.extend(f"context:{value}" for value in context_errors)
    materials = context.get("materials", {}) if isinstance(context, dict) else {}
    spine = materials.get("argument_spine", {}).get("content", "")
    headings = [match.group(1).strip() for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", spine)]
    groups = {
        "introduction": ("introduction", "引言", "绪论"),
        "methods": ("method", "materials", "方法", "试验", "实验", "数值模型"),
        "results": ("result", "结果"),
        "discussion": ("discussion", "讨论"),
        "conclusion": ("conclusion", "结论"),
    }
    heading_text = " ".join(headings).lower()
    missing_groups = [name for name, words in groups.items() if not any(word.lower() in heading_text for word in words)]
    if missing_groups:
        errors.extend(f"argument_spine_missing_section:{name}" for name in missing_groups)
    if len(headings) < 5:
        errors.append("argument_spine_too_shallow")
    checks["argument_spine"] = {"heading_count": len(headings), "missing_section_groups": missing_groups}

    evidence = materials.get("evidence_ledger", {})
    mapping = materials.get("claim_citation_map", {})
    if int(evidence.get("row_count", 0) or 0) < 1:
        errors.append("evidence_ledger_empty")
    if int(mapping.get("row_count", 0) or 0) < 1:
        errors.append("claim_citation_map_empty")
    if int(evidence.get("unresolved_status_count", 0) or 0):
        warnings.append(f"evidence_ledger_unresolved:{evidence['unresolved_status_count']}")
    if int(mapping.get("unresolved_status_count", 0) or 0):
        warnings.append(f"claim_map_unresolved:{mapping['unresolved_status_count']}")
    checks["evidence"] = {
        "ledger_rows": evidence.get("row_count", 0),
        "mapping_rows": mapping.get("row_count", 0),
        "ledger_unresolved": evidence.get("unresolved_status_count", 0),
        "mapping_unresolved": mapping.get("unresolved_status_count", 0),
    }

    method_text = materials.get("method_reproducibility", {}).get("content", "")
    required_method_topics = {
        "conditions_or_boundaries": ("boundary", "condition", "边界", "条件", "应力路径", "温度", "压力"),
        "parameters_or_samples": ("parameter", "sample", "参数", "试样", "样品", "岩性"),
        "validation_or_repeatability": ("validation", "calibration", "replicate", "验证", "校准", "重复"),
        "software_or_instrument": ("software", "version", "instrument", "软件", "版本", "仪器"),
    }
    method_lower = method_text.lower()
    missing_method_topics = [
        name for name, words in required_method_topics.items() if not any(word.lower() in method_lower for word in words)
    ]
    if missing_method_topics:
        warnings.extend(f"method_audit_missing_topic:{name}" for name in missing_method_topics)
    checks["method_reproducibility"] = {"missing_topics": missing_method_topics}

    report = {
        "schema_version": 1,
        "check": "plan",
        "generated_at": utc_now(),
        "status": "valid" if not errors else "invalid",
        "vault": str(vault),
        "context": str(context_path),
        "checks": checks,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }
    destination = Path(args.output).expanduser().resolve() if args.output else vault / "08_Quality" / "Manuscript_Plan_Audit.json"
    return write_or_emit(report, destination, args.check_only)


def extract_invariants(text: str) -> dict[str, Counter[str]]:
    polarity_patterns = {
        "increase": r"\b(?:increase[sd]?|increasing|higher|rise[sn]?|rose|enhanc(?:e[sd]?|ing))\b|增加|升高|上升|增强",
        "decrease": r"\b(?:decrease[sd]?|decreasing|lower|decline[sd]?|reduc(?:e[sd]?|ing))\b|降低|下降|减小|削弱",
        "no_change": r"\b(?:no\s+(?:significant\s+)?change|unchanged|stable)\b|无显著变化|保持不变|稳定",
    }
    strength_patterns = {
        "causal": r"\b(?:cause[sd]?|lead[sd]?\s+to|result[sd]?\s+in|determin(?:e[sd]?|ing))\b|导致|引起|决定",
        "association": r"\b(?:associat(?:e[sd]?|ion)|correlat(?:e[sd]?|ion)|linked?\s+to)\b|相关|关联",
        "uncertainty": r"\b(?:may|might|could|suggest[sd]?|indicat(?:e[sd]?|ing)|likely)\b|可能|或许|表明|暗示",
        "proof": r"\b(?:prove[sd]?|conclusive(?:ly)?|demonstrate[sd]?)\b|证明|证实|决定性",
    }
    return {
        "numbers_and_units": Counter(norm_token(value) for value in NUMBER_RE.findall(text)),
        "dois": Counter(norm_token(value.rstrip(".,;:)]}")) for value in DOI_RE.findall(text)),
        "figure_table_equation_refs": Counter(norm_token(value) for value in FIGURE_REF_RE.findall(text)),
        "numeric_citations": Counter(norm_token(value) for value in CITATION_RE.findall(text)),
        "trace_ids": Counter(norm_token(value) for value in ID_RE.findall(text)),
        "polarity": Counter(
            {name: len(re.findall(pattern, text, flags=re.IGNORECASE)) for name, pattern in polarity_patterns.items()}
        ),
        "claim_strength": Counter(
            {name: len(re.findall(pattern, text, flags=re.IGNORECASE)) for name, pattern in strength_patterns.items()}
        ),
    }


def counter_delta(before: Counter[str], after: Counter[str]) -> dict[str, dict[str, int]]:
    removed = before - after
    added = after - before
    return {"removed": dict(sorted(removed.items())), "added": dict(sorted(added.items()))}


def invariants_audit(args: argparse.Namespace) -> int:
    before_path = Path(args.before).expanduser().resolve()
    after_path = Path(args.after).expanduser().resolve()
    if not before_path.is_file() or not after_path.is_file():
        missing = [str(path) for path in (before_path, after_path) if not path.is_file()]
        raise ValueError(f"missing_revision_file:{'|'.join(missing)}")
    before_text = before_path.read_text(encoding="utf-8-sig")
    after_text = after_path.read_text(encoding="utf-8-sig")
    before = extract_invariants(before_text)
    after = extract_invariants(after_text)
    differences = {name: counter_delta(before[name], after[name]) for name in before}
    errors: list[str] = []
    strict_categories = {
        "numbers_and_units",
        "dois",
        "figure_table_equation_refs",
        "numeric_citations",
        "trace_ids",
    }
    if not args.allow_language_token_changes:
        strict_categories.update({"polarity", "claim_strength"})
    for category in sorted(strict_categories):
        delta = differences[category]
        if delta["removed"] or delta["added"]:
            errors.append(f"invariant_changed:{category}")
    hits = placeholder_hits(after_text)
    if hits:
        errors.append(f"placeholder_in_revised_text:{','.join(hits)}")
    report = {
        "schema_version": 1,
        "check": "invariants",
        "generated_at": utc_now(),
        "status": "valid" if not errors else "invalid",
        "before": str(before_path),
        "after": str(after_path),
        "strict_categories": sorted(strict_categories),
        "differences": differences,
        "errors": errors,
        "warnings": [] if not args.allow_language_token_changes else ["polarity_and_claim_strength_not_blocking"],
    }
    if args.output:
        destination = Path(args.output).expanduser().resolve()
    elif args.vault:
        destination = resolve_vault(args.vault) / "08_Quality" / "Language_Invariant_Audit.json"
    else:
        destination = after_path.parent / "Language_Invariant_Audit.json"
    return write_or_emit(report, destination, args.check_only)


def numeric_reference_sets(text: str) -> tuple[set[int], set[int]]:
    reference_heading = re.search(r"(?im)^#{1,6}\s+(?:references|bibliography|参考文献)\s*$", text)
    body = text[: reference_heading.start()] if reference_heading else text
    references = text[reference_heading.end() :] if reference_heading else ""
    cited: set[int] = set()
    for token in CITATION_RE.findall(body):
        numbers = [int(value) for value in re.findall(r"\d+", token)]
        if len(numbers) == 2 and re.search(r"[-–—]", token):
            start, end = numbers
            if 0 < start <= end <= start + 500:
                cited.update(range(start, end + 1))
        else:
            cited.update(numbers)
    listed: set[int] = set()
    for match in re.finditer(r"(?m)^\s*(?:\[(\d+)\]|(\d+)[.)])\s+", references):
        listed.add(int(match.group(1) or match.group(2)))
    return cited, listed


def table_integrity(path: Path, label: str) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not path.is_file():
        return {"path": str(path), "exists": False}, [f"missing:{label}"], warnings
    fields, rows = read_csv(path)
    if not rows:
        errors.append(f"empty:{label}")
    id_field = find_field(fields, ("claim_id", "evidence_id", "paper_id", "id"))
    ids: list[str] = []
    if id_field:
        ids = [(row.get(id_field) or "").strip() for row in rows]
        if any(not value for value in ids):
            errors.append(f"empty_id:{label}")
        if len([value for value in ids if value]) != len(set(value for value in ids if value)):
            errors.append(f"duplicate_id:{label}")
    else:
        warnings.append(f"no_id_column:{label}")
    status_field = find_field(fields, ("status", "evidence_status", "support_status", "verification_status"))
    unresolved_rows: list[int] = []
    if status_field:
        unresolved = {"", "pending", "unverified", "needs_evidence", "blocked", "metadata-only", "metadata_only"}
        unresolved_rows = [index for index, row in enumerate(rows, start=2) if (row.get(status_field) or "").strip().lower() in unresolved]
        if unresolved_rows:
            errors.append(f"unresolved_status:{label}:{len(unresolved_rows)}")
    return {
        "path": str(path),
        "exists": True,
        "columns": fields,
        "row_count": len(rows),
        "id_field": id_field,
        "ids": [value for value in ids if value],
        "status_field": status_field,
        "unresolved_rows": unresolved_rows,
    }, errors, warnings


def figure_integrity(vault: Path, manuscript_text: str) -> tuple[dict[str, Any], list[str], list[str]]:
    path = vault / "07_Manuscript" / "figure_register.csv"
    if not path.is_file():
        return {"exists": False, "path": str(path)}, ["missing:figure_register"], []
    fields, rows = read_csv(path)
    errors: list[str] = []
    warnings: list[str] = []
    issues: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        figure_id = (row.get("figure_id") or f"row-{index}").strip()
        output = (row.get("output_path") or "").strip()
        qa = (row.get("qa_status") or "").strip().lower()
        placeholder = (row.get("placeholder_token") or "").strip()
        figure_type = (row.get("figure_type") or "").strip().lower()
        ready = qa in {"pass", "passed", "valid", "approved", "completed"}
        if output:
            raw = Path(output).expanduser()
            output_path = raw.resolve() if raw.is_absolute() else (vault / raw).resolve()
            if not output_path.is_file():
                errors.append(f"figure_output_missing:{figure_id}")
                issues.append({"figure_id": figure_id, "issue": "output_missing", "path": str(output_path)})
            if not ready:
                errors.append(f"figure_qa_not_passed:{figure_id}")
        elif "mechanism" in figure_type or "机制" in figure_type:
            if not placeholder:
                errors.append(f"mechanism_placeholder_missing:{figure_id}")
            elif placeholder not in manuscript_text:
                errors.append(f"mechanism_placeholder_not_in_manuscript:{figure_id}")
        else:
            errors.append(f"figure_output_unset:{figure_id}")
    if not rows:
        warnings.append("figure_register_empty")
    return {"exists": True, "path": str(path), "columns": fields, "row_count": len(rows), "issues": issues}, errors, warnings


def integrity_audit(args: argparse.Namespace) -> int:
    vault = resolve_vault(args.vault)
    manuscript_path = select_manuscript(vault, args.manuscript)
    text = manuscript_path.read_text(encoding="utf-8-sig")
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    hits = placeholder_hits(text)
    if hits:
        errors.append(f"manuscript_placeholders:{','.join(hits)}")
    if len(text.strip()) < 500:
        errors.append("manuscript_too_short")
    checks["manuscript"] = {"path": str(manuscript_path), "characters": len(text), "placeholder_hits": hits}

    cited, listed = numeric_reference_sets(text)
    reference_heading_exists = bool(re.search(r"(?im)^#{1,6}\s+(?:references|bibliography|参考文献)\s*$", text))
    if not reference_heading_exists:
        errors.append("reference_section_missing")
    missing_references = sorted(cited - listed)
    uncited_references = sorted(listed - cited)
    if missing_references:
        errors.append(f"cited_reference_missing:{','.join(map(str, missing_references))}")
    if uncited_references:
        warnings.append(f"listed_but_uncited:{','.join(map(str, uncited_references))}")
    dois = [norm_token(value.rstrip(".,;:)]}")) for value in DOI_RE.findall(text)]
    duplicate_dois = sorted(value for value, count in Counter(dois).items() if count > 1)
    if duplicate_dois:
        warnings.append(f"duplicate_doi_occurrence:{','.join(duplicate_dois)}")
    checks["citations"] = {
        "numeric_citations": sorted(cited),
        "listed_numeric_references": sorted(listed),
        "missing_references": missing_references,
        "uncited_references": uncited_references,
        "duplicate_dois": duplicate_dois,
    }

    ledger, found_errors, found_warnings = table_integrity(
        vault / "05_Knowledge" / "Evidence" / "Evidence_Ledger.csv", "evidence_ledger"
    )
    errors.extend(found_errors)
    warnings.extend(found_warnings)
    mapping, found_errors, found_warnings = table_integrity(
        vault / "05_Knowledge" / "Evidence" / "Claim_Citation_Map.csv", "claim_citation_map"
    )
    errors.extend(found_errors)
    warnings.extend(found_warnings)
    ledger_ids = set(ledger.get("ids", []))
    mapping_ids = set(mapping.get("ids", []))
    if ledger.get("id_field") == "claim_id" and mapping.get("id_field") == "claim_id":
        unmapped = sorted(ledger_ids - mapping_ids)
        unknown = sorted(mapping_ids - ledger_ids)
        if unmapped:
            errors.append(f"claims_without_citation_mapping:{','.join(unmapped[:30])}")
        if unknown:
            errors.append(f"mapping_unknown_claims:{','.join(unknown[:30])}")
    checks["evidence_ledger"] = ledger
    checks["claim_citation_map"] = mapping

    figure_check, found_errors, found_warnings = figure_integrity(vault, text)
    errors.extend(found_errors)
    warnings.extend(found_warnings)
    checks["figures"] = figure_check

    manifest = load_json_yaml(vault / "00_Control" / "project_manifest.yaml")
    title = str(manifest.get("working_title", "")).strip()
    first_h1 = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    if title and not placeholder_hits(title):
        manuscript_title = norm_space(first_h1.group(1)) if first_h1 else ""
        if norm_token(title) != norm_token(manuscript_title):
            errors.append("working_title_mismatch")
    checks["title"] = {"manifest": title, "manuscript": first_h1.group(1).strip() if first_h1 else ""}

    open_priority: list[str] = []
    for rel in ("08_Quality/Final_Consistency_Audit.md", "08_Quality/Reviewer_Simulation.md"):
        path = vault / rel
        if not path.is_file():
            errors.append(f"missing:{rel}")
            continue
        quality_text = path.read_text(encoding="utf-8-sig")
        for line in quality_text.splitlines():
            if re.search(
                r"\b(?:no|none)\s+open\b|\b(?:resolved|closed|passed|cleared)\b|"
                r"无(?:未解决|开放|待处理)|已(?:解决|关闭|通过|清零)",
                line,
                flags=re.IGNORECASE,
            ):
                continue
            if re.search(r"\bP[01]\b", line, flags=re.IGNORECASE) and (
                re.search(r"\[ \]", line)
                or re.search(r"\b(?:open|pending|unresolved|fail|blocked)\b|未解决|待修复|未关闭|阻塞", line, flags=re.IGNORECASE)
            ):
                open_priority.append(f"{rel}:{line.strip()[:240]}")
    if open_priority:
        errors.append(f"open_p0_p1:{len(open_priority)}")
    checks["priority_issues"] = open_priority

    report = {
        "schema_version": 1,
        "check": "integrity",
        "generated_at": utc_now(),
        "status": "valid" if not errors else "invalid",
        "vault": str(vault),
        "checks": checks,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }
    destination = Path(args.output).expanduser().resolve() if args.output else vault / "08_Quality" / "Final_Integrity_Audit.json"
    return write_or_emit(report, destination, args.check_only)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="正文计划、不变量和投稿前完整性检查。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="检查正文写作上下文与章节计划")
    plan.add_argument("--vault", required=True)
    plan.add_argument("--context")
    plan.add_argument("--output")
    plan.add_argument("--check-only", action="store_true")

    invariants = subparsers.add_parser("invariants", help="比较改写前后的科学不变量")
    invariants.add_argument("--before", required=True)
    invariants.add_argument("--after", required=True)
    invariants.add_argument("--vault")
    invariants.add_argument("--output")
    invariants.add_argument("--allow-language-token-changes", action="store_true")
    invariants.add_argument("--check-only", action="store_true")

    integrity = subparsers.add_parser("integrity", help="检查正文、证据、图件和高优先级问题闭环")
    integrity.add_argument("--vault", required=True)
    integrity.add_argument("--manuscript")
    integrity.add_argument("--output")
    integrity.add_argument("--check-only", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "plan":
            return plan_audit(args)
        if args.command == "invariants":
            return invariants_audit(args)
        return integrity_audit(args)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        emit({"status": "error", "check": args.command, "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
