"""Shared deterministic helpers for the paper workflow plugin."""

from __future__ import annotations

import json
import os
import tempfile
import csv
from pathlib import Path
from typing import Any


STAGES = ["S0", "S1", "S2", "S3", "S4", "S4.5", "S5", "S5.5", "S6", "S7", "S8", "S9", "S10", "S10.5", "S11", "S12", "S13", "S14"]
ALLOWED_STATUSES = {
    "not_started", "in_progress", "blocked_by_user", "blocked_by_evidence",
    "blocked_by_runtime", "completed",
}


def resolve_vault(value: str) -> Path:
    vault = Path(value).expanduser().resolve()
    if not vault.is_dir():
        raise ValueError(f"论文库不存在或不是文件夹：{vault}")
    return vault


def load_json_yaml(path: Path) -> dict[str, Any]:
    """Load a JSON-shaped document saved with a .yaml extension."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"缺少状态文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"状态文件不是本插件支持的 JSON-shaped YAML：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"状态文件顶层必须是对象：{path}")
    return data


def atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temp_name).replace(path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def atomic_csv_write(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        Path(temp_name).replace(path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def emit(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
