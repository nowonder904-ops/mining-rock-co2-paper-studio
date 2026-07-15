#!/usr/bin/env python3
"""Deterministically begin, block, validate, and complete workflow stages."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from workflow_common import ALLOWED_STATUSES, STAGES, atomic_csv_write, atomic_json_write, emit, load_json_yaml, resolve_vault


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def paths(vault: Path) -> tuple[Path, Path, Path]:
    return (
        vault / "00_Control" / "pipeline_state.yaml",
        vault / "00_Control" / "decision_ledger.md",
        vault / "00_Control" / "artifact_register.csv",
    )


def load_state(vault: Path) -> tuple[Path, dict]:
    state_path, _, _ = paths(vault)
    state = load_json_yaml(state_path)
    state.setdefault("stages", {stage: "not_started" for stage in STAGES})
    state.setdefault("blockers", {})
    state.setdefault("stage_runs", {})
    return state_path, state


def save_state(path: Path, state: dict) -> None:
    state["updated"] = now_iso()
    atomic_json_write(path, state)


def append_decision(vault: Path, stage: str, decision: str, actor: str, evidence: str, impact: str) -> None:
    _, ledger, _ = paths(vault)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    if not ledger.exists():
        ledger.write_text("# 决策账本\n\n| 日期 | 阶段 | 决策 | 决策人 | 依据 | 影响 |\n|---|---|---|---|---|---|\n", encoding="utf-8")
    def safe(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ").strip()
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"| {now_iso()} | {safe(stage)} | {safe(decision)} | {safe(actor)} | {safe(evidence)} | {safe(impact)} |\n")


def read_artifacts(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    fields = ["artifact_id", "stage", "path", "artifact_type", "status", "validator", "updated", "sha256", "notes"]
    if not path.exists() or path.stat().st_size == 0:
        return fields, []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return fields, [{field: row.get(field, "") for field in fields} for row in rows]


def begin(args, vault: Path) -> int:
    state_path, state = load_state(vault)
    current = state["stages"].get(args.stage, "not_started")
    if current == "completed" and not args.reopen:
        raise ValueError(f"{args.stage} 已完成；如确需重开必须显式使用 --reopen 并记录原因。")
    index = STAGES.index(args.stage)
    if index > 0:
        predecessor = STAGES[index - 1]
        if state["stages"].get(predecessor) != "completed" and not args.allow_entry:
            raise ValueError(f"前置阶段 {predecessor} 尚未 completed。")
    state["stages"][args.stage] = "in_progress"
    state["current_stage"] = args.stage
    state["blockers"].pop(args.stage, None)
    state["stage_runs"][args.stage] = {
        "started_at": now_iso(), "inputs": args.input or [], "outputs": [], "validator": "", "next_stage": "",
    }
    save_state(state_path, state)
    append_decision(vault, args.stage, "开始或重开阶段", args.actor, args.reason or "阶段合同允许", "进入 in_progress")
    emit({"status": "in_progress", "stage": args.stage, "state": str(state_path)})
    return 0


def block(args, vault: Path) -> int:
    if args.status not in {"blocked_by_user", "blocked_by_evidence", "blocked_by_runtime"}:
        raise ValueError("阻塞状态无效。")
    state_path, state = load_state(vault)
    state["stages"][args.stage] = args.status
    state["current_stage"] = args.stage
    state["blockers"][args.stage] = {"status": args.status, "reason": args.reason, "at": now_iso()}
    save_state(state_path, state)
    append_decision(vault, args.stage, f"阶段阻塞：{args.status}", args.actor, args.reason, "停止向下游推进")
    emit({"status": args.status, "stage": args.stage, "reason": args.reason})
    return 1


def run_validator(vault: Path, stage: str) -> tuple[bool, dict]:
    script = Path(__file__).resolve().parent / "validate_stage_outputs.py"
    process = subprocess.run(
        [sys.executable, str(script), "--vault", str(vault), "--stage", stage],
        text=True, capture_output=True, encoding="utf-8", errors="replace", check=False,
    )
    try:
        report = json.loads(process.stdout.strip())
    except json.JSONDecodeError:
        report = {"status": "error", "stdout": process.stdout, "stderr": process.stderr, "returncode": process.returncode}
    return process.returncode == 0 and report.get("status") == "valid", report


def complete(args, vault: Path) -> int:
    state_path, state = load_state(vault)
    if state["stages"].get(args.stage) not in {"in_progress", "blocked_by_evidence", "blocked_by_runtime"}:
        raise ValueError(f"{args.stage} 当前状态不允许完成：{state['stages'].get(args.stage)}")
    valid, report = run_validator(vault, args.stage)
    if not valid:
        state["stages"][args.stage] = "blocked_by_evidence"
        state["blockers"][args.stage] = {"status": "blocked_by_evidence", "reason": "阶段验证未通过", "report": report, "at": now_iso()}
        save_state(state_path, state)
        emit({"status": "blocked_by_evidence", "stage": args.stage, "validation": report})
        return 1
    state["stages"][args.stage] = "completed"
    state["blockers"].pop(args.stage, None)
    index = STAGES.index(args.stage)
    next_stage = STAGES[index + 1] if index + 1 < len(STAGES) else ""
    state["current_stage"] = next_stage or args.stage
    run = state["stage_runs"].setdefault(args.stage, {})
    run.update({"completed_at": now_iso(), "outputs": args.output or [], "validator": "validate_stage_outputs.py", "validation": report, "next_stage": next_stage})
    save_state(state_path, state)
    append_decision(vault, args.stage, "阶段验收完成", args.actor, "确定性验证通过", f"下一阶段：{next_stage or '无'}")
    emit({"status": "completed", "stage": args.stage, "next_stage": next_stage, "validation": report})
    return 0


def decision(args, vault: Path) -> int:
    append_decision(vault, args.stage, args.decision, args.actor, args.evidence, args.impact)
    emit({"status": "recorded", "type": "decision", "stage": args.stage})
    return 0


def artifact(args, vault: Path) -> int:
    _, _, register = paths(vault)
    fields, rows = read_artifacts(register)
    existing = next((row for row in rows if row.get("artifact_id") == args.artifact_id), None)
    row = existing or {field: "" for field in fields}
    row.update({
        "artifact_id": args.artifact_id, "stage": args.stage, "path": args.path,
        "artifact_type": args.artifact_type, "status": args.status, "validator": args.validator,
        "updated": now_iso(), "sha256": args.sha256, "notes": args.notes,
    })
    if existing is None:
        rows.append(row)
    atomic_csv_write(register, fields, rows)
    emit({"status": "recorded", "type": "artifact", "artifact_id": args.artifact_id})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="管理论文工作流阶段、决策和产物账册。")
    sub = parser.add_subparsers(dest="command", required=True)
    begin_p = sub.add_parser("begin")
    begin_p.add_argument("--vault", required=True); begin_p.add_argument("--stage", choices=STAGES, required=True)
    begin_p.add_argument("--input", action="append"); begin_p.add_argument("--actor", default="Codex")
    begin_p.add_argument("--reason", default=""); begin_p.add_argument("--reopen", action="store_true"); begin_p.add_argument("--allow-entry", action="store_true")
    begin_p.set_defaults(func=begin)
    block_p = sub.add_parser("block")
    block_p.add_argument("--vault", required=True); block_p.add_argument("--stage", choices=STAGES, required=True)
    block_p.add_argument("--status", choices=["blocked_by_user", "blocked_by_evidence", "blocked_by_runtime"], required=True)
    block_p.add_argument("--reason", required=True); block_p.add_argument("--actor", default="Codex"); block_p.set_defaults(func=block)
    complete_p = sub.add_parser("complete")
    complete_p.add_argument("--vault", required=True); complete_p.add_argument("--stage", choices=STAGES, required=True)
    complete_p.add_argument("--output", action="append"); complete_p.add_argument("--actor", default="Codex"); complete_p.set_defaults(func=complete)
    decision_p = sub.add_parser("decision")
    decision_p.add_argument("--vault", required=True); decision_p.add_argument("--stage", required=True)
    decision_p.add_argument("--decision", required=True); decision_p.add_argument("--actor", default="user")
    decision_p.add_argument("--evidence", default=""); decision_p.add_argument("--impact", default=""); decision_p.set_defaults(func=decision)
    artifact_p = sub.add_parser("artifact")
    artifact_p.add_argument("--vault", required=True); artifact_p.add_argument("--stage", required=True)
    artifact_p.add_argument("--artifact-id", required=True); artifact_p.add_argument("--path", required=True)
    artifact_p.add_argument("--artifact-type", required=True); artifact_p.add_argument("--status", default="created")
    artifact_p.add_argument("--validator", default=""); artifact_p.add_argument("--sha256", default=""); artifact_p.add_argument("--notes", default="")
    artifact_p.set_defaults(func=artifact)
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        return args.func(args, vault)
    except (OSError, ValueError, json.JSONDecodeError, csv.Error) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
