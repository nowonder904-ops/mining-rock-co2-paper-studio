#!/usr/bin/env python3
"""Migrate a legacy vault to the standalone schema with a reversible backup."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from literature_register import FIELDS as LITERATURE_FIELDS
from workflow_common import STAGES, atomic_csv_write, atomic_json_write, emit, load_json_yaml, resolve_vault


MIGRATIONS = {
    "03_Literature/literature_register.csv": LITERATURE_FIELDS,
    "00_Control/artifact_register.csv": ["artifact_id", "stage", "path", "artifact_type", "status", "validator", "updated", "sha256", "notes"],
    "00_Control/mode_b_decisions.csv": ["paper_id", "suggested_grade", "confirmed_grade", "order", "batch_id", "figure_table_level", "confirmed_at", "confirmed_by", "note"],
    "02_Search/search_log.csv": ["search_id", "run_at", "database", "query", "time_window", "filters", "result_count", "export_path", "status", "notes"],
}


def csv_migration(path: Path, fields: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists() or path.stat().st_size == 0: return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle); old_fields = reader.fieldnames or []; rows = list(reader)
    return old_fields, [{field: row.get(field, "") for field in fields} for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="把旧论文库迁移到独立插件 schema v2；默认先 dry-run。")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--apply", action="store_true", help="实际迁移；使用前应先向用户展示 dry-run 并确认")
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        changes = []
        for rel, fields in MIGRATIONS.items():
            path = vault / rel
            old_fields, rows = csv_migration(path, fields)
            if old_fields and old_fields != fields:
                changes.append({"path": rel, "type": "csv-schema", "old_fields": old_fields, "new_fields": fields, "rows": len(rows)})
        state_path = vault / "00_Control" / "pipeline_state.yaml"
        state = load_json_yaml(state_path)
        if state.get("schema_version") != 2 or state.get("workflow_architecture") != "standalone": changes.append({"path": str(state_path.relative_to(vault)), "type": "state-schema-v2"})
        manifest_path = vault / "00_Control" / "project_manifest.yaml"
        manifest = load_json_yaml(manifest_path)
        if manifest.get("schema_version") != 2 or manifest.get("workflow_architecture") != "standalone": changes.append({"path": str(manifest_path.relative_to(vault)), "type": "manifest-schema-v2"})
        plugin_root = Path(__file__).resolve().parents[1]
        template_root = plugin_root / "assets" / "vault-template"
        missing_templates = [str(path.relative_to(template_root)) for path in template_root.rglob("*") if path.is_file() and not (vault / path.relative_to(template_root)).exists()]
        if missing_templates: changes.append({"type": "missing-templates", "count": len(missing_templates), "paths": missing_templates})
        if not args.apply:
            emit({"status": "dry_run", "vault": str(vault), "changes": changes, "will_backup": bool(changes), "next": "获得用户确认后使用 --apply"})
            return 0
        if not changes:
            emit({"status": "already_current", "vault": str(vault)})
            return 0
        backup = vault / "00_Control" / "migrations" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-pre-standalone-v2")
        backup.mkdir(parents=True, exist_ok=False)
        for rel in [*MIGRATIONS.keys(), "00_Control/pipeline_state.yaml", "00_Control/project_manifest.yaml"]:
            source = vault / rel
            if source.is_file():
                destination = backup / rel; destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)
        for rel, fields in MIGRATIONS.items():
            path = vault / rel; old_fields, rows = csv_migration(path, fields)
            if old_fields and old_fields != fields: atomic_csv_write(path, fields, rows)
        state["schema_version"] = 2; state["workflow_architecture"] = "standalone"
        state.setdefault("blockers", {}); state.setdefault("stage_runs", {})
        state["stages"] = {stage: state.get("stages", {}).get(stage, "not_started") for stage in STAGES}
        atomic_json_write(state_path, state)
        manifest["schema_version"] = 2; manifest["workflow_architecture"] = "standalone"
        manifest.setdefault("citation_style", "[待填写]"); manifest.setdefault("word_template_path", "")
        manifest.setdefault("authoritative_data_paths", []); manifest.setdefault("authoritative_manuscript", "")
        atomic_json_write(manifest_path, manifest)
        init_script = plugin_root / "scripts" / "init_paper_vault.py"
        process = subprocess.run([sys.executable, str(init_script), "--parent-dir", str(vault.parent), "--vault-name", vault.name, "--reuse-existing"], text=True, capture_output=True, encoding="utf-8", errors="replace", check=False)
        if process.returncode != 0: raise ValueError(f"补齐模板失败：{process.stdout}\n{process.stderr}")
        emit({"status": "migrated", "vault": str(vault), "backup": str(backup), "changes": changes})
        return 0
    except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
