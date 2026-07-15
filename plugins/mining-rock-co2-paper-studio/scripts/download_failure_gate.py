#!/usr/bin/env python3
"""Build and verify the manual full-text recovery gate after download failures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from workflow_common import STAGES, atomic_csv_write, atomic_json_write, emit, load_json_yaml, resolve_vault


REGISTER_REL = Path("03_Literature/literature_register.csv")
FAILURE_CSV_REL = Path("03_Literature/download_failures.csv")
FAILURE_MD_REL = Path("03_Literature/download_failures.md")
STATE_REL = Path("00_Control/pipeline_state.yaml")
MANUAL_INBOX_REL = Path("03_Literature/Manual_Inbox")
FAILURE_STATUSES = {
    "download-failed": "automatic_download_failed",
    "manual-download-required": "manual_download_required",
    "paywalled": "paywall_or_subscription",
    "login-required": "institutional_login_required",
    "captcha": "captcha_or_interactive_check",
}
FAILURE_FIELDS = [
    "paper_id", "title", "year", "doi", "journal", "failure_reason", "source_url",
    "expected_filename", "manual_inbox_path", "status", "resolved_pdf_path",
    "identity_status", "identity_evidence", "identity_verified_at", "reported_at",
    "verified_at", "notes",
]
REGISTER_REQUIRED_FIELDS = [
    "source_url", "screening_status", "fulltext_status", "pdf_path", "download_error",
    "identity_status", "identity_evidence", "identity_verified_at",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise ValueError(f"缺少 CSV：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def safe_paper_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    if not cleaned:
        raise ValueError("文献记录缺少可用 paper_id，不能生成稳定的人工下载文件名。")
    return cleaned


def pipe_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def row_is_resolved(row: dict[str, str]) -> bool:
    status = row.get("status", "").strip()
    if status == "excluded":
        return bool(row.get("notes", "").strip())
    return (
        status == "verified"
        and row.get("identity_status", "").strip() == "matched"
        and bool(row.get("identity_evidence", "").strip())
    )


def write_report(vault: Path, rows: list[dict[str, str]], unresolved: list[dict[str, str]]) -> None:
    inbox = vault / MANUAL_INBOX_REL
    lines = [
        "# 自动下载失败清单",
        "",
        f"- 生成时间：{now_iso()}",
        f"- 失败记录：{len(rows)}",
        f"- 尚未解决：{len(unresolved)}",
        f"- 人工下载目录：`{inbox}`",
        "",
    ]
    if rows:
        lines.extend([
            "## 你需要做什么",
            "",
            "1. 通过学校、机构订阅、作者主页、开放获取仓库或其他合法渠道获取 PDF；不要绕过访问控制。",
            "2. 按清单中的 `expected_filename` 重命名，并放入人工下载目录；如保留其他位置，由 Codex 在你通知后登记实际路径。",
            "3. 全部处理完成后明确告知 Codex“人工下载已完成”。在此之前流程保持暂停。",
            "4. Codex 将逐份检查文件存在、体积、PDF 首尾标记，并核对首页题名及 DOI/出版信息与文献登记关系；任一冲突都继续阻塞。",
            "",
            "| Paper ID | 题名 | DOI | 失败原因 | 期望文件 | 身份核对 | 状态 |",
            "|---|---|---|---|---|---|---|",
        ])
        for row in rows:
            lines.append(
                "| {paper_id} | {title} | {doi} | {reason} | `{filename}` | {identity} | {status} |".format(
                    paper_id=pipe_text(row.get("paper_id", "")),
                    title=pipe_text(row.get("title", "")),
                    doi=pipe_text(row.get("doi", "")),
                    reason=pipe_text(row.get("failure_reason", "")),
                    filename=pipe_text(row.get("expected_filename", "")),
                    identity=pipe_text(row.get("identity_status", "") or "pending"),
                    status=pipe_text(row.get("status", "")),
                )
            )
    else:
        lines.extend(["没有自动下载失败项，S4.5 可直接通过。", ""])
    (vault / FAILURE_MD_REL).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def update_gate_state(vault: Path, blocked: bool, unresolved_count: int) -> dict:
    path = vault / STATE_REL
    state = load_json_yaml(path)
    old_stages = state.get("stages")
    if not isinstance(old_stages, dict):
        raise ValueError("pipeline_state.yaml 缺少 stages 对象。")
    stages = {stage: old_stages.get(stage, "not_started") for stage in STAGES}
    for name, value in old_stages.items():
        if name not in stages:
            stages[name] = value
    if stages.get("S4") != "completed":
        raise ValueError("必须先完成 S4 检索与自动下载记录，才能进入 S4.5 人工补全文闸门。")
    stages["S4.5"] = "blocked_by_user" if blocked else "completed"
    state["stages"] = stages
    blockers = state.get("blockers") if isinstance(state.get("blockers"), dict) else {}
    if blocked:
        blockers["S4.5"] = f"等待用户人工补齐 {unresolved_count} 篇自动下载失败文献，并明确通知 Codex。"
        state["current_stage"] = "S4.5"
    else:
        blockers.pop("S4.5", None)
        if state.get("current_stage") in {"S4", "S4.5"}:
            state["current_stage"] = "S5"
    state["blockers"] = blockers
    state["updated"] = now_iso()
    atomic_json_write(path, state)
    return state


def resolve_pdf_path(vault: Path, value: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    resolved = (vault / raw).resolve()
    if not resolved.is_relative_to(vault):
        raise ValueError(f"相对 PDF 路径越出论文库：{value}")
    return resolved


def portable_path(vault: Path, path: Path) -> str:
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return str(path)


def valid_pdf(path: Path) -> tuple[bool, str]:
    if path.suffix.lower() != ".pdf":
        return False, "扩展名不是 .pdf"
    if not path.is_file():
        return False, "文件不存在"
    try:
        if path.stat().st_size < 512:
            return False, "文件小于 512 字节，不符合论文 PDF 的最低结构检查"
        with path.open("rb") as handle:
            signature = handle.read(5)
        if signature != b"%PDF-":
            return False, "文件头不是 %PDF-，可能不是有效 PDF"
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 4096))
            tail = handle.read()
        if b"%%EOF" not in tail:
            return False, "文件尾缺少 %%EOF，PDF 可能被截断"
    except OSError as exc:
        return False, f"无法读取文件：{exc}"
    return True, "ok"


def build_gate(vault: Path) -> int:
    register_fields, register_rows = load_table(vault / REGISTER_REL)
    existing_rows: list[dict[str, str]] = []
    failure_path = vault / FAILURE_CSV_REL
    if failure_path.is_file():
        _, existing_rows = load_table(failure_path)
    by_id = {row.get("paper_id", ""): {field: row.get(field, "") for field in FAILURE_FIELDS} for row in existing_rows if row.get("paper_id")}
    reported_at = now_iso()

    for paper in register_rows:
        fulltext_status = paper.get("fulltext_status", "").strip()
        download_error = paper.get("download_error", "").strip()
        pdf_path = paper.get("pdf_path", "").strip()
        reason = download_error or FAILURE_STATUSES.get(fulltext_status, "")
        if fulltext_status == "pdf-verified":
            if not pdf_path:
                reason = "registered_pdf_missing: pdf_path is empty"
            else:
                try:
                    ok, detail = valid_pdf(resolve_pdf_path(vault, pdf_path))
                except ValueError as exc:
                    ok, detail = False, str(exc)
                if not ok:
                    reason = f"registered_pdf_invalid: {detail}"
        if not reason:
            continue
        paper_id = safe_paper_id(paper.get("paper_id", ""))
        current = by_id.get(paper_id, {field: "" for field in FAILURE_FIELDS})
        expected = f"{paper_id}.pdf"
        existing_status = current.get("status", "").strip()
        current.update({
            "paper_id": paper_id,
            "title": paper.get("title", "").strip(),
            "year": paper.get("year", "").strip(),
            "doi": paper.get("doi", "").strip(),
            "journal": paper.get("journal", "").strip(),
            "failure_reason": reason,
            "source_url": paper.get("source_url", "").strip(),
            "expected_filename": expected,
            "manual_inbox_path": (MANUAL_INBOX_REL / expected).as_posix(),
            "status": "excluded" if existing_status == "excluded" else "pending",
            "reported_at": current.get("reported_at", "").strip() or reported_at,
        })
        by_id[paper_id] = current

    rows = list(by_id.values())
    rows.sort(key=lambda item: (item.get("status", "") in {"verified", "excluded"}, item.get("paper_id", "")))
    unresolved = [row for row in rows if not row_is_resolved(row)]
    (vault / MANUAL_INBOX_REL).mkdir(parents=True, exist_ok=True)
    atomic_csv_write(failure_path, FAILURE_FIELDS, rows)
    write_report(vault, rows, unresolved)
    update_gate_state(vault, bool(unresolved), len(unresolved))
    emit({
        "status": "blocked_by_user" if unresolved else "completed",
        "stage": "S4.5",
        "failure_count": len(rows),
        "unresolved_count": len(unresolved),
        "failure_list": str(failure_path),
        "human_report": str(vault / FAILURE_MD_REL),
        "manual_inbox": str(vault / MANUAL_INBOX_REL),
        "instruction": "如有未解决项，停止流程并等待用户明确通知人工下载已完成。",
    })
    return 1 if unresolved else 0


def verify_gate(vault: Path) -> int:
    failure_fields, rows = load_table(vault / FAILURE_CSV_REL)
    register_fields, register_rows = load_table(vault / REGISTER_REL)
    for field in REGISTER_REQUIRED_FIELDS:
        if field not in register_fields:
            register_fields.append(field)
    register_by_id = {row.get("paper_id", ""): row for row in register_rows if row.get("paper_id")}
    unresolved: list[dict[str, str]] = []
    verified_at = now_iso()

    for row in rows:
        paper_id = row.get("paper_id", "").strip()
        register_row = register_by_id.get(paper_id)
        if not register_row:
            unresolved.append({"paper_id": paper_id, "reason": "literature_register 中找不到对应记录"})
            continue
        if row.get("status", "").strip() == "excluded":
            if not row.get("notes", "").strip():
                unresolved.append({"paper_id": paper_id, "reason": "标记 excluded 时必须填写 notes"})
                continue
            register_row["screening_status"] = "exclude"
            continue
        candidate = row.get("resolved_pdf_path", "").strip() or row.get("manual_inbox_path", "").strip()
        if not candidate:
            unresolved.append({"paper_id": paper_id, "reason": "没有 PDF 路径"})
            continue
        try:
            pdf_path = resolve_pdf_path(vault, candidate)
            ok, detail = valid_pdf(pdf_path)
        except ValueError as exc:
            ok, detail = False, str(exc)
            pdf_path = vault
        if not ok:
            row["status"] = "pending"
            unresolved.append({"paper_id": paper_id, "reason": detail, "expected": candidate})
            continue
        resolved_value = portable_path(vault, pdf_path)
        row["resolved_pdf_path"] = resolved_value
        if row.get("identity_status", "").strip() != "matched":
            row["status"] = "downloaded"
            unresolved.append({
                "paper_id": paper_id,
                "reason": "PDF 结构有效，但尚未核对首页题名或 DOI；完成核对后运行 confirm-identity。",
                "expected": resolved_value,
            })
            continue
        row["status"] = "verified"
        row["verified_at"] = verified_at
        register_row["fulltext_status"] = "pdf-verified"
        register_row["pdf_path"] = resolved_value
        register_row["download_error"] = ""
        register_row["identity_status"] = "matched"
        register_row["identity_evidence"] = row.get("identity_evidence", "")
        register_row["identity_verified_at"] = row.get("identity_verified_at", "") or verified_at
        if "content_hash" not in register_fields:
            register_fields.append("content_hash")
        register_row["content_hash"] = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    normalized_failure_fields = FAILURE_FIELDS + [field for field in failure_fields if field not in FAILURE_FIELDS]
    atomic_csv_write(vault / FAILURE_CSV_REL, normalized_failure_fields, rows)
    atomic_csv_write(vault / REGISTER_REL, register_fields, register_rows)
    write_report(vault, rows, unresolved)
    update_gate_state(vault, bool(unresolved), len(unresolved))
    emit({
        "status": "blocked_by_user" if unresolved else "completed",
        "stage": "S4.5",
        "verified_count": sum(1 for row in rows if row.get("status") == "verified"),
        "excluded_count": sum(1 for row in rows if row.get("status") == "excluded"),
        "unresolved": unresolved,
        "next_stage": None if unresolved else "S5",
    })
    return 1 if unresolved else 0


def show_status(vault: Path) -> int:
    failure_path = vault / FAILURE_CSV_REL
    if not failure_path.is_file():
        emit({"status": "not_built", "stage": "S4.5", "message": "尚未生成自动下载失败清单。"})
        return 1
    _, rows = load_table(failure_path)
    unresolved = [row for row in rows if not row_is_resolved(row)]
    emit({
        "status": "blocked_by_user" if unresolved else "completed",
        "stage": "S4.5",
        "failure_count": len(rows),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "human_report": str(vault / FAILURE_MD_REL),
        "manual_inbox": str(vault / MANUAL_INBOX_REL),
    })
    return 1 if unresolved else 0


def set_resolved_path(vault: Path, paper_id: str, value: str) -> int:
    fields, rows = load_table(vault / FAILURE_CSV_REL)
    matched = next((row for row in rows if row.get("paper_id", "").strip() == paper_id.strip()), None)
    if matched is None:
        raise ValueError(f"下载失败清单中找不到 paper_id：{paper_id}")
    pdf_path = resolve_pdf_path(vault, value)
    matched["resolved_pdf_path"] = portable_path(vault, pdf_path)
    matched["status"] = "downloaded"
    normalized_fields = FAILURE_FIELDS + [field for field in fields if field not in FAILURE_FIELDS]
    atomic_csv_write(vault / FAILURE_CSV_REL, normalized_fields, rows)
    emit({"status": "path_recorded", "paper_id": paper_id, "resolved_pdf_path": matched["resolved_pdf_path"], "next": "run verify"})
    return 0


def exclude_entry(vault: Path, paper_id: str, reason: str) -> int:
    fields, rows = load_table(vault / FAILURE_CSV_REL)
    matched = next((row for row in rows if row.get("paper_id", "").strip() == paper_id.strip()), None)
    if matched is None:
        raise ValueError(f"下载失败清单中找不到 paper_id：{paper_id}")
    if not reason.strip():
        raise ValueError("排除文献必须提供非空原因。")
    matched["status"] = "excluded"
    matched["notes"] = reason.strip()
    normalized_fields = FAILURE_FIELDS + [field for field in fields if field not in FAILURE_FIELDS]
    atomic_csv_write(vault / FAILURE_CSV_REL, normalized_fields, rows)
    emit({"status": "exclusion_recorded", "paper_id": paper_id, "reason": reason.strip(), "next": "run verify"})
    return 0


def confirm_identity(vault: Path, paper_id: str, evidence: str) -> int:
    fields, rows = load_table(vault / FAILURE_CSV_REL)
    matched = next((row for row in rows if row.get("paper_id", "").strip() == paper_id.strip()), None)
    if matched is None:
        raise ValueError(f"下载失败清单中找不到 paper_id：{paper_id}")
    if not evidence.strip():
        raise ValueError("必须记录首页题名、DOI 或出版元数据的核对依据。")
    candidate = matched.get("resolved_pdf_path", "").strip() or matched.get("manual_inbox_path", "").strip()
    if not candidate:
        raise ValueError("尚未登记 PDF 路径，不能确认文献身份。")
    pdf_path = resolve_pdf_path(vault, candidate)
    ok, detail = valid_pdf(pdf_path)
    if not ok:
        raise ValueError(f"PDF 结构检查未通过：{detail}")
    matched["identity_status"] = "matched"
    matched["identity_evidence"] = evidence.strip()
    matched["identity_verified_at"] = now_iso()
    matched["resolved_pdf_path"] = portable_path(vault, pdf_path)
    matched["status"] = "downloaded"
    normalized_fields = FAILURE_FIELDS + [field for field in fields if field not in FAILURE_FIELDS]
    atomic_csv_write(vault / FAILURE_CSV_REL, normalized_fields, rows)
    emit({"status": "identity_confirmed", "paper_id": paper_id, "evidence": evidence.strip(), "next": "run verify"})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="管理自动下载失败后的人工补全文闸门。")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify", "status"):
        item = sub.add_parser(command)
        item.add_argument("--vault", required=True)
    set_path_parser = sub.add_parser("set-path")
    set_path_parser.add_argument("--vault", required=True)
    set_path_parser.add_argument("--paper-id", required=True)
    set_path_parser.add_argument("--path", required=True)
    exclude_parser = sub.add_parser("exclude")
    exclude_parser.add_argument("--vault", required=True)
    exclude_parser.add_argument("--paper-id", required=True)
    exclude_parser.add_argument("--reason", required=True)
    identity_parser = sub.add_parser("confirm-identity")
    identity_parser.add_argument("--vault", required=True)
    identity_parser.add_argument("--paper-id", required=True)
    identity_parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        if args.command == "build":
            return build_gate(vault)
        if args.command == "verify":
            return verify_gate(vault)
        if args.command == "status":
            return show_status(vault)
        if args.command == "set-path":
            return set_resolved_path(vault, args.paper_id, args.path)
        if args.command == "exclude":
            return exclude_entry(vault, args.paper_id, args.reason)
        return confirm_identity(vault, args.paper_id, args.evidence)
    except (OSError, ValueError, csv.Error) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
