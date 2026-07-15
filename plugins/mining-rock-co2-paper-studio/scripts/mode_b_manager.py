#!/usr/bin/env python3
"""Prepare and validate the mandatory user-confirmed Mode B reading batch."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from literature_register import load_rows, save_rows
from workflow_common import atomic_csv_write, atomic_json_write, emit, load_json_yaml, resolve_vault


DECISION_FIELDS = ["paper_id", "suggested_grade", "confirmed_grade", "order", "batch_id", "figure_table_level", "confirmed_at", "confirmed_by", "note"]
DEFAULT_DEFINITIONS = {
    "A": "直接决定研究问题、核心机制或主要方法；完整精读并逐图逐表核查。",
    "B": "强支撑核心主张或关键方法；完整精读，按相关性核查图表。",
    "C": "提供背景、参数范围或对照；结构化阅读并提取可引用证据。",
    "D": "低相关、重复、全文不可核验或不满足纳排标准；保留排除理由，不进入精读。",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def control_paths(vault: Path) -> tuple[Path, Path, Path, Path]:
    control = vault / "00_Control"
    return control / "mode_b_candidates.csv", control / "mode_b_decisions.csv", control / "mode_b_config.json", control / "pipeline_state.yaml"


def set_stage(vault: Path, status: str, reason: str = "") -> None:
    _, _, _, state_path = control_paths(vault)
    state = load_json_yaml(state_path)
    state.setdefault("stages", {})["S5.5"] = status
    state["current_stage"] = "S5.5"
    state.setdefault("blockers", {})
    if reason:
        state["blockers"]["S5.5"] = {"status": status, "reason": reason, "at": now_iso()}
    else:
        state["blockers"].pop("S5.5", None)
    state["updated"] = now_iso()
    atomic_json_write(state_path, state)


def prepare(args, vault: Path) -> int:
    candidates_path, _, config_path, _ = control_paths(vault)
    register = vault / "03_Literature" / "literature_register.csv"
    rows = load_rows(register)
    candidates = []
    for row in rows:
        if row.get("screening_status") not in {"include", "priority"}: continue
        candidates.append({
            "paper_id": row.get("paper_id", ""), "title": row.get("title", ""), "year": row.get("year", ""),
            "doi": row.get("doi", ""), "domain": row.get("domain", ""), "fulltext_status": row.get("fulltext_status", ""),
            "suggested_grade": row.get("suggested_grade", ""), "suggestion_basis": "", "user_grade": "", "user_order": "", "user_note": "",
        })
    fields = ["paper_id", "title", "year", "doi", "domain", "fulltext_status", "suggested_grade", "suggestion_basis", "user_grade", "user_order", "user_note"]
    atomic_csv_write(candidates_path, fields, candidates)
    atomic_json_write(config_path, {
        "schema_version": 2, "definitions": DEFAULT_DEFINITIONS, "definitions_mode": "default-pending-user-confirmation",
        "batch_limit": None, "figure_table_level": None, "batch_id": "", "user_confirmed": False,
        "confirmed_by": "", "confirmed_at": "", "note": "等待用户确认分级、顺序、批量上限和图表要求。",
    })
    set_stage(vault, "blocked_by_user", "等待用户确认 Mode B 分级、顺序、批量上限和逐图逐表要求。")
    emit({"status": "blocked_by_user", "candidate_count": len(candidates), "candidate_table": str(candidates_path), "config": str(config_path), "question": "请确认本批 Mode B 的 A/B/C/D 定义与逐篇等级、精读顺序、批量上限，以及是否要求逐图逐表分析；确认前不会进入精读。"})
    return 1


def read_user_table(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_decisions(vault: Path) -> tuple[list[str], dict, list[dict[str, str]]]:
    _, decisions_path, config_path, _ = control_paths(vault)
    errors = []
    if not config_path.is_file(): return ["缺少 mode_b_config.json。"], {}, []
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("user_confirmed"): errors.append("用户确认尚未记录。")
    try: batch_limit = int(config.get("batch_limit"))
    except (TypeError, ValueError): batch_limit = 0; errors.append("batch_limit 无效。")
    if config.get("figure_table_level") not in {"none", "relevant", "all"}: errors.append("figure_table_level 无效。")
    if not decisions_path.is_file(): return errors + ["缺少 mode_b_decisions.csv。"], config, []
    rows = read_user_table(decisions_path)
    if not rows: errors.append("决策表为空。")
    orders = set()
    selected_count = 0
    register_rows = load_rows(vault / "03_Literature" / "literature_register.csv")
    register = {row.get("paper_id", ""): row for row in register_rows}
    for index, row in enumerate(rows, start=2):
        paper_id = row.get("paper_id", "").strip()
        grade = row.get("confirmed_grade", "").strip().upper()
        order = row.get("order", "").strip()
        if paper_id not in register: errors.append(f"第 {index} 行 paper_id 不在登记表：{paper_id}")
        if grade not in {"A", "B", "C", "D"}: errors.append(f"第 {index} 行等级无效：{grade}")
        if grade in {"A", "B", "C"}:
            selected_count += 1
            if register.get(paper_id, {}).get("fulltext_status") != "pdf-verified": errors.append(f"{paper_id} 未达到 pdf-verified，不能进入 {grade} 级精读。")
            if not order.isdigit() or int(order) <= 0: errors.append(f"{paper_id} 缺少有效精读顺序。")
            elif order in orders: errors.append(f"精读顺序重复：{order}")
            orders.add(order)
        if grade == "D" and not row.get("note", "").strip(): errors.append(f"{paper_id} 为 D 级但未记录理由。")
    if batch_limit and selected_count > batch_limit: errors.append(f"A/B/C 数量 {selected_count} 超过用户上限 {batch_limit}。")
    return errors, config, rows


def confirm(args, vault: Path) -> int:
    candidates_path, decisions_path, config_path, _ = control_paths(vault)
    source = Path(args.decisions).expanduser().resolve()
    if not source.is_file(): raise ValueError(f"用户确认表不存在：{source}")
    incoming = read_user_table(source)
    register_path = vault / "03_Literature" / "literature_register.csv"
    register_rows = load_rows(register_path)
    register = {row.get("paper_id", ""): row for row in register_rows}
    batch_id = args.batch_id or "MODEB-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    confirmed_at = now_iso()
    decisions = []
    for raw in incoming:
        paper_id = (raw.get("paper_id") or "").strip()
        if not paper_id: continue
        if paper_id not in register: raise ValueError(f"paper_id 不在文献登记表：{paper_id}")
        grade = (raw.get("confirmed_grade") or raw.get("user_grade") or "").strip().upper()
        order = (raw.get("order") or raw.get("user_order") or "").strip()
        decisions.append({
            "paper_id": paper_id, "suggested_grade": register[paper_id].get("suggested_grade", ""),
            "confirmed_grade": grade, "order": order, "batch_id": batch_id,
            "figure_table_level": args.figure_table_level, "confirmed_at": confirmed_at,
            "confirmed_by": args.confirmed_by, "note": (raw.get("note") or raw.get("user_note") or "").strip(),
        })
        register[paper_id]["confirmed_grade"] = grade
        register[paper_id]["last_transition_at"] = confirmed_at
    atomic_csv_write(decisions_path, DECISION_FIELDS, decisions)
    save_rows(register_path, register_rows)
    definitions = DEFAULT_DEFINITIONS if args.definitions_mode == "default" else {"custom": args.definitions_note}
    atomic_json_write(config_path, {
        "schema_version": 2, "definitions": definitions, "definitions_mode": args.definitions_mode,
        "batch_limit": args.batch_limit, "figure_table_level": args.figure_table_level, "batch_id": batch_id,
        "user_confirmed": True, "confirmed_by": args.confirmed_by, "confirmed_at": confirmed_at,
        "note": args.definitions_note,
    })
    errors, config, rows = validate_decisions(vault)
    if errors:
        set_stage(vault, "blocked_by_user", "Mode B 用户确认记录未通过验证。")
        emit({"status": "blocked_by_user", "errors": errors, "decision_table": str(decisions_path)})
        return 1
    set_stage(vault, "in_progress")
    emit({"status": "confirmed", "batch_id": batch_id, "selected": sum(1 for row in rows if row.get("confirmed_grade") in {"A", "B", "C"}), "decision_table": str(decisions_path), "config": config})
    return 0


def validate(args, vault: Path) -> int:
    errors, config, rows = validate_decisions(vault)
    emit({"status": "valid" if not errors else "invalid", "errors": errors, "count": len(rows), "config": config})
    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="管理用户确认的 Mode B 文献分级与精读批次。")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_p = sub.add_parser("prepare"); prepare_p.add_argument("--vault", required=True); prepare_p.set_defaults(func=prepare)
    confirm_p = sub.add_parser("confirm"); confirm_p.add_argument("--vault", required=True); confirm_p.add_argument("--decisions", required=True)
    confirm_p.add_argument("--batch-limit", type=int, required=True); confirm_p.add_argument("--figure-table-level", choices=["none", "relevant", "all"], required=True)
    confirm_p.add_argument("--definitions-mode", choices=["default", "custom"], required=True); confirm_p.add_argument("--definitions-note", default="")
    confirm_p.add_argument("--batch-id", default=""); confirm_p.add_argument("--confirmed-by", default="user", choices=["user"]); confirm_p.set_defaults(func=confirm)
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
