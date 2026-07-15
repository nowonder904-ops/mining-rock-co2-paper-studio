#!/usr/bin/env python3
"""Build a compact, traceable manuscript context from validated vault artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow_common import atomic_json_write, emit, load_json_yaml, resolve_vault


SCHEMA_VERSION = 1
ALLOWED_DOMAINS = {"mining-engineering", "rock-mechanics", "co2-storage"}
PLACEHOLDERS = (
    "[待填写]",
    "[待补充]",
    "{{",
    "AUTHOR_INPUT_NEEDED",
    "TBD",
    "TODO",
    "未形成可核查内容",
)
CORE_MARKDOWN = {
    "research_question": "01_Scoping/RQ_Card.md",
    "research_trajectory": "06_Review/Research_Trajectory.md",
    "research_gaps": "06_Review/Research_Gaps.md",
    "contribution_boundary": "06_Review/Contribution_Confirmation.md",
    "argument_spine": "07_Manuscript/Argument_Spine.md",
    "method_reproducibility": "08_Quality/Methods_Reproducibility_Audit.md",
}
CORE_TABLES = {
    "evidence_ledger": "05_Knowledge/Evidence/Evidence_Ledger.csv",
    "claim_citation_map": "05_Knowledge/Evidence/Claim_Citation_Map.csv",
}
OPTIONAL_INPUTS = {
    "citation_cards": "04_Reading/Citation_Cards/Citation_Cards.md",
    "figure_register": "07_Manuscript/figure_register.csv",
    "mechanism_figure_prompts": "07_Manuscript/Mechanism_Figure_Prompts.md",
    "decision_ledger": "00_Control/decision_ledger.md",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault).as_posix()


def placeholder_hits(text: str) -> list[str]:
    return sorted(marker for marker in PLACEHOLDERS if marker.lower() in text.lower())


def headings(text: str) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text)]


def markdown_material(vault: Path, rel: str, *, max_chars: int = 24000) -> tuple[dict[str, Any], list[str]]:
    path = vault / rel
    errors: list[str] = []
    if not path.is_file():
        return {"path": rel, "exists": False}, [f"missing:{rel}"]
    text = path.read_text(encoding="utf-8-sig")
    hits = placeholder_hits(text)
    if len(text.strip()) < 48:
        errors.append(f"too_short:{rel}")
    if hits:
        errors.append(f"placeholder:{rel}:{','.join(hits)}")
    item = {
        "path": rel,
        "exists": True,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "headings": headings(text),
        "placeholder_hits": hits,
        "content": text[:max_chars],
        "content_truncated": len(text) > max_chars,
    }
    return item, errors


def first_present(fieldnames: list[str], choices: tuple[str, ...]) -> str | None:
    lowered = {name.lower(): name for name in fieldnames}
    for choice in choices:
        if choice.lower() in lowered:
            return lowered[choice.lower()]
    return None


def csv_material(vault: Path, rel: str) -> tuple[dict[str, Any], list[str]]:
    path = vault / rel
    errors: list[str] = []
    if not path.is_file():
        return {"path": rel, "exists": False}, [f"missing:{rel}"]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        errors.append(f"missing_header:{rel}")
    if not rows:
        errors.append(f"empty_table:{rel}")
    id_field = first_present(fieldnames, ("claim_id", "evidence_id", "paper_id", "id"))
    empty_ids = 0
    ids: list[str] = []
    if id_field:
        for row in rows:
            value = (row.get(id_field) or "").strip()
            if value:
                ids.append(value)
            else:
                empty_ids += 1
        if empty_ids:
            errors.append(f"empty_id:{rel}:{empty_ids}")
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate_id:{rel}")
    unresolved = 0
    status_field = first_present(fieldnames, ("status", "evidence_status", "support_status", "verification_status"))
    if status_field:
        unresolved_values = {"", "pending", "unverified", "needs_evidence", "blocked", "metadata-only", "metadata_only"}
        unresolved = sum(1 for row in rows if (row.get(status_field) or "").strip().lower() in unresolved_values)
    item = {
        "path": rel,
        "exists": True,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "columns": fieldnames,
        "row_count": len(rows),
        "id_field": id_field,
        "sample_ids": ids[:50],
        "empty_id_count": empty_ids,
        "unresolved_status_count": unresolved,
    }
    return item, errors


def optional_material(vault: Path, rel: str) -> dict[str, Any]:
    path = vault / rel
    if not path.is_file():
        return {"path": rel, "exists": False}
    return {
        "path": rel,
        "exists": True,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def build_context(vault: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = vault / "00_Control" / "project_manifest.yaml"
    manifest = load_json_yaml(manifest_path)
    domains = {str(value).strip().lower() for value in manifest.get("domain_scope", []) if str(value).strip()}
    if not domains:
        errors.append("manifest:domain_scope_missing")
    outside = sorted(domains - ALLOWED_DOMAINS)
    if outside:
        errors.append(f"manifest:domain_scope_outside_plugin:{','.join(outside)}")
    if not (domains & ALLOWED_DOMAINS):
        errors.append("manifest:no_supported_domain")

    manifest_placeholders = {
        key: placeholder_hits(str(manifest.get(key, "")))
        for key in ("working_title", "manuscript_language", "target_journal")
    }
    for key, hits in manifest_placeholders.items():
        if hits:
            warnings.append(f"manifest:{key}_not_confirmed")

    materials: dict[str, Any] = {}
    for name, rel in CORE_MARKDOWN.items():
        materials[name], found = markdown_material(vault, rel)
        errors.extend(found)
    for name, rel in CORE_TABLES.items():
        materials[name], found = csv_material(vault, rel)
        errors.extend(found)
    for name, rel in OPTIONAL_INPUTS.items():
        materials[name] = optional_material(vault, rel)

    context = {
        "schema_version": SCHEMA_VERSION,
        "workflow": "mining-rock-co2-paper-lifecycle",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vault": str(vault),
        "domain_scope": sorted(domains),
        "project": {
            "project_name": manifest.get("project_name", ""),
            "working_title": manifest.get("working_title", ""),
            "manuscript_language": manifest.get("manuscript_language", ""),
            "target_journal": manifest.get("target_journal", ""),
            "manifest_path": relative(vault, manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
        },
        "materials": materials,
        "writing_contract": {
            "evidence_first": True,
            "missing_information_policy": "preserve_explicit_placeholder_and_block_unsupported_claim",
            "required_claim_dimensions": [
                "object",
                "scale",
                "conditions",
                "evidence_type",
                "direction",
                "magnitude_or_range",
                "boundary",
            ],
            "forbidden_transfers": [
                "laboratory_correlation_to_field_causality",
                "idealized_simulation_to_site_validation",
                "short_term_test_to_long_term_storage_security",
                "cross_geology_transfer_without_boundary_analysis",
            ],
        },
        "validation": {
            "status": "ready" if not errors else "blocked_by_evidence",
            "errors": sorted(set(errors)),
            "warnings": sorted(set(warnings)),
        },
    }
    return context, errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="构建可追溯、可恢复的正文写作上下文。")
    parser.add_argument("--vault", required=True, help="论文知识库绝对路径")
    parser.add_argument("--output", help="输出 JSON；默认 07_Manuscript/manuscript_context.json")
    parser.add_argument("--check-only", action="store_true", help="只检查，不写入文件")
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        context, errors, _warnings = build_context(vault)
        destination = Path(args.output).expanduser().resolve() if args.output else vault / "07_Manuscript" / "manuscript_context.json"
        if not args.check_only and not errors:
            atomic_json_write(destination, context)
        emit(
            {
                "status": "ready" if not errors else "blocked_by_evidence",
                "written": str(destination) if not args.check_only and not errors else None,
                "context": context,
            }
        )
        return 0 if not errors else 1
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
