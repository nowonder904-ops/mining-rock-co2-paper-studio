#!/usr/bin/env python3
"""Safely initialize a stage-gated Obsidian paper vault."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import date
from pathlib import Path

from workflow_common import STAGES, atomic_json_write, emit


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = PLUGIN_ROOT / "assets" / "vault-template"
DIRECTORIES = [
    "00_Control",
    "01_Scoping",
    "02_Search",
    "02_Search/runs",
    "03_Literature/PDF/Mining_Engineering",
    "03_Literature/PDF/Rock_Mechanics",
    "03_Literature/PDF/CO2_Storage",
    "03_Literature/PDF/Cross_Domain",
    "03_Literature/Manual_Inbox",
    "03_Literature/Download_Cache",
    "04_Reading/Notes",
    "04_Reading/Citation_Cards",
    "04_Reading/Extracted_Text",
    "05_Knowledge/Wiki",
    "05_Knowledge/Evidence",
    "06_Review",
    "07_Manuscript",
    "07_Manuscript/Figures",
    "07_Manuscript/Figures/Preview",
    "07_Manuscript/Data",
    "08_Quality",
    "09_Delivery",
    "09_Delivery/Submission",
    "10_Revision",
    "90_Maps",
    "91_Bases",
]
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("论文库名称不能为空或为相对路径标记。")
    if Path(cleaned).is_absolute() or "/" in cleaned or "\\" in cleaned:
        raise ValueError("--vault-name 只能是文件夹名称，父路径请通过 --parent-dir 提供。")
    if re.search(r'[<>:"|?*]', cleaned) or cleaned.rstrip(" .") != cleaned:
        raise ValueError("论文库名称含 Windows 不允许的字符或结尾。")
    if cleaned.split(".")[0].upper() in WINDOWS_RESERVED:
        raise ValueError("论文库名称是 Windows 保留名称。")
    return cleaned


def replace_tokens(text: str, vault_name: str, vault_path: Path) -> str:
    return (
        text.replace("{{VAULT_NAME}}", vault_name)
        .replace("{{VAULT_PATH}}", str(vault_path))
        .replace("{{CREATED_DATE}}", date.today().isoformat())
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化采矿—岩石力学—CO₂ 论文知识库。")
    parser.add_argument("--parent-dir", required=True, help="已经由用户确认的父目录绝对路径")
    parser.add_argument("--vault-name", required=True, help="已经由用户确认的论文库文件夹名称")
    parser.add_argument("--reuse-existing", action="store_true", help="仅补齐已有库缺失的模板，绝不覆盖文件")
    parser.add_argument("--dry-run", action="store_true", help="仅显示将创建的内容")
    args = parser.parse_args()

    try:
        name = validate_name(args.vault_name)
        parent = Path(args.parent_dir).expanduser()
        if not parent.is_absolute():
            raise ValueError("--parent-dir 必须是绝对路径。")
        parent = parent.resolve()
        if not parent.is_dir():
            raise ValueError(f"父目录不存在或不是文件夹：{parent}")
        target = (parent / name).resolve()
        if target.parent != parent:
            raise ValueError("解析后的论文库路径超出用户确认的父目录。")
        nonempty = target.exists() and any(target.iterdir())
        if nonempty and not args.reuse_existing:
            raise ValueError("同名论文库已存在且非空；请人工确认后再使用 --reuse-existing。")
        if not TEMPLATE_ROOT.is_dir():
            raise ValueError(f"插件模板目录缺失：{TEMPLATE_ROOT}")

        template_files = [p for p in TEMPLATE_ROOT.rglob("*") if p.is_file()]
        planned = [str(target / d) for d in DIRECTORIES]
        planned.extend(str(target / p.relative_to(TEMPLATE_ROOT)) for p in template_files)
        if args.dry_run:
            emit({"status": "dry_run", "vault": str(target), "would_create_or_fill": planned})
            return 0

        target.mkdir(parents=False, exist_ok=True)
        for rel in DIRECTORIES:
            (target / rel).mkdir(parents=True, exist_ok=True)

        created_files: list[str] = []
        skipped_files: list[str] = []
        for source in template_files:
            rel = source.relative_to(TEMPLATE_ROOT)
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                skipped_files.append(str(rel))
                continue
            if source.suffix.lower() in {".md", ".yaml", ".yml", ".base", ".canvas", ".csv", ".txt"}:
                destination.write_text(replace_tokens(source.read_text(encoding="utf-8"), name, target), encoding="utf-8", newline="\n")
            else:
                shutil.copy2(source, destination)
            created_files.append(str(rel))

        created_paths = {target / Path(item) for item in created_files}
        state_path = target / "00_Control" / "pipeline_state.yaml"
        if state_path in created_paths:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["vault_name"] = name
            state["vault_path"] = str(target)
            state["updated"] = date.today().isoformat()
            state["current_stage"] = "S2"
            state["stages"] = {stage: ("completed" if stage in {"S0", "S1"} else "not_started") for stage in STAGES}
            state["blockers"] = {}
            state["stage_runs"] = {}
            atomic_json_write(state_path, state)

        manifest_path = target / "00_Control" / "project_manifest.yaml"
        if manifest_path in created_paths:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["project_name"] = name
            manifest["vault_path"] = str(target)
            manifest["created"] = date.today().isoformat()
            atomic_json_write(manifest_path, manifest)

        emit({"status": "created", "vault": str(target), "created_files": created_files, "preserved_existing_files": skipped_files})
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
