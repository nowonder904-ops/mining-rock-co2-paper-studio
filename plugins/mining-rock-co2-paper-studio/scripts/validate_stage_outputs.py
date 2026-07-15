#!/usr/bin/env python3
"""Check required artifacts for one workflow stage without changing the vault."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from workflow_common import STAGES, emit, load_json_yaml, resolve_vault


REQUIRED = {
    "S0": ["00_Control/decision_ledger.md"],
    "S1": ["00_Control/project_manifest.yaml", "00_Control/pipeline_state.yaml", "00_Control/task_board.md"],
    "S2": ["01_Scoping/RQ_Card.md", "01_Scoping/Innovation_Matrix.md", "01_Scoping/Reviewer_Challenges.md"],
    "S3": ["02_Search/Search_Strategy.md"],
    "S4": ["02_Search/search_log.csv", "03_Literature/literature_register.csv"],
    "S4.5": ["03_Literature/download_failures.csv", "03_Literature/download_failures.md"],
    "S5": ["03_Literature/literature_register.csv"],
    "S5.5": ["00_Control/mode_b_decisions.csv"],
    "S6": ["04_Reading/Citation_Cards/Citation_Cards.md", "05_Knowledge/Evidence/Evidence_Ledger.csv", "05_Knowledge/Wiki/Index.md", "90_Maps/Mechanism_Evidence_Map.canvas"],
    "S7": ["06_Review/Research_Trajectory.md", "06_Review/Research_Gaps.md"],
    "S8": ["06_Review/Contribution_Confirmation.md"],
    "S9": ["07_Manuscript/Argument_Spine.md", "05_Knowledge/Evidence/Claim_Citation_Map.csv", "08_Quality/Methods_Reproducibility_Audit.md"],
    "S10": ["07_Manuscript/Manuscript_Draft.md"],
    "S10.5": ["07_Manuscript/Manuscript_Draft.md", "07_Manuscript/figure_register.csv", "07_Manuscript/Mechanism_Figure_Prompts.md"],
    "S11": ["07_Manuscript/Manuscript_Polished.md", "07_Manuscript/Manuscript_Humanized.md"],
    "S12": ["08_Quality/Final_Consistency_Audit.md", "08_Quality/Reviewer_Simulation.md"],
    "S13": ["09_Delivery/delivery_manifest.json", "00_Control/artifact_register.csv"],
    "S14": ["10_Revision/response_tracker.csv", "10_Revision/Reviewer_Response.md", "10_Revision/Revised_Manuscript.md"],
}


def mode_b_errors(path) -> list[str]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return ["Mode B 决策表没有用户确认记录。"]
    required = {"paper_id", "confirmed_grade", "order", "batch_id", "figure_table_level", "confirmed_at"}
    errors = []
    for index, row in enumerate(rows, start=2):
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            errors.append(f"Mode B 决策表第 {index} 行缺少：{', '.join(sorted(missing))}")
        if row.get("confirmed_grade") not in {"A", "B", "C", "D"}:
            errors.append(f"Mode B 决策表第 {index} 行等级无效。")
    return errors


def fulltext_gate_errors(vault) -> list[str]:
    errors = []
    state = load_json_yaml(vault / "00_Control" / "pipeline_state.yaml")
    stages = state.get("stages", {})
    if stages.get("S4.5") != "completed":
        errors.append(f"S4.5 状态必须为 completed，当前为：{stages.get('S4.5')}")
    path = vault / "03_Literature" / "download_failures.csv"
    if not path.is_file():
        return errors
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for index, row in enumerate(rows, start=2):
        status = row.get("status", "").strip()
        paper_id = row.get("paper_id", "").strip() or f"row-{index}"
        if status == "excluded":
            if not row.get("notes", "").strip():
                errors.append(f"{paper_id} 标记 excluded 但没有说明原因。")
            continue
        if status != "verified":
            errors.append(f"{paper_id} 尚未 verified：{status or 'empty'}")
            continue
        if row.get("identity_status", "").strip() != "matched" or not row.get("identity_evidence", "").strip():
            errors.append(f"{paper_id} 缺少题名/DOI 身份核对记录。")
            continue
        value = row.get("resolved_pdf_path", "").strip()
        if not value:
            errors.append(f"{paper_id} 缺少 resolved_pdf_path。")
            continue
        raw = Path(value).expanduser()
        pdf_path = raw.resolve() if raw.is_absolute() else (vault / raw).resolve()
        if not raw.is_absolute() and not pdf_path.is_relative_to(vault):
            errors.append(f"{paper_id} 的相对 PDF 路径越出论文库。")
            continue
        try:
            with pdf_path.open("rb") as handle:
                signature = handle.read(5)
        except OSError as exc:
            errors.append(f"{paper_id} 的 PDF 无法读取：{exc}")
            continue
        try:
            size = pdf_path.stat().st_size
            with pdf_path.open("rb") as handle:
                handle.seek(max(0, size - 4096))
                tail = handle.read()
        except OSError as exc:
            errors.append(f"{paper_id} 的 PDF 结构无法检查：{exc}")
            continue
        if pdf_path.suffix.lower() != ".pdf" or signature != b"%PDF-" or size < 512 or b"%%EOF" not in tail:
            errors.append(f"{paper_id} 的文件不是可验证 PDF：{pdf_path}")
    return errors


def artifact_is_ready(path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".md":
        text = path.read_text(encoding="utf-8")
        return len(text.strip()) >= 48 and "[待填写]" not in text
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _ in csv.reader(handle)) >= 2
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data)
    if suffix == ".canvas":
        data = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(data, ensure_ascii=False)
        return bool(data.get("nodes")) and "待" not in text
    return path.stat().st_size > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="检查指定阶段的必需产物。")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--stage", required=True, choices=STAGES)
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        missing = [rel for rel in REQUIRED[args.stage] if not (vault / rel).is_file()]
        unready = [] if args.stage in {"S0", "S1", "S4.5"} else [
            rel for rel in REQUIRED[args.stage]
            if (vault / rel).is_file() and not artifact_is_ready(vault / rel)
        ]
        errors = []
        if args.stage == "S5.5":
            errors.extend(mode_b_errors(vault / "00_Control" / "mode_b_decisions.csv"))
        if args.stage == "S4.5":
            errors.extend(fulltext_gate_errors(vault))
        emit({"status": "valid" if not missing and not unready and not errors else "invalid", "stage": args.stage, "missing": missing, "unready": unready, "errors": errors})
        return 0 if not missing and not unready and not errors else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
