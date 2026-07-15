#!/usr/bin/env python3
"""Validate standalone data figures and evidence-bounded mechanism prompts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from workflow_common import emit, resolve_vault


REGISTER_FIELDS = {
    "figure_id", "figure_type", "claim_id", "claim", "source_data_path", "target_journal",
    "vector_path", "output_path", "preview_path", "grayscale_path", "qa_report_path",
    "qa_status", "caption_path", "statistics_disclosure", "prompt_id", "placeholder_token",
    "data_hash", "notes",
}
VECTOR_SUFFIXES = {".svg", ".pdf", ".eps"}
CAPTION_FIELDS = {"caption", "sample_size", "units", "error_definition", "statistical_test", "multiple_comparison"}
PROMPT_LABELS = {
    "核心主张", "核心机制", "证据映射", "对象与尺度", "空间布局", "观察视角",
    "作用方向与顺序", "边界条件", "物理量、单位与符号", "分面结构",
    "标签、配色与字体", "禁止元素", "图注限制与类比边界",
}
PLACEHOLDERS = ("[待填写]", "{{", "TODO", "FIXME")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def locate(vault: Path, value: str) -> Path:
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (vault / path).resolve()
    if not path.is_absolute() and not resolved.is_relative_to(vault):
        raise ValueError(f"相对路径越出论文库：{value}")
    return resolved


def artifact_by_path(qa: dict[str, Any], path: Path) -> dict[str, Any] | None:
    for artifact in (qa.get("artifacts") or {}).values():
        try:
            candidate = Path(str(artifact.get("path", ""))).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if candidate == path.resolve():
            return artifact
    return None


def validate_hash(path: Path, record: dict[str, Any] | None, label: str, errors: list[str]) -> None:
    if record is None:
        errors.append(f"QA JSON 未登记 {label}：{path}")
        return
    expected = str(record.get("sha256", "")).strip()
    if not expected or expected != sha256_file(path):
        errors.append(f"{label} 哈希与 QA JSON 不一致：{path}")


def prompt_record(text: str, prompt_id: str) -> str | None:
    pattern = re.compile(
        rf"<!-- MECHANISM_PROMPT_BEGIN: {re.escape(prompt_id)} -->(.*?)<!-- MECHANISM_PROMPT_END: {re.escape(prompt_id)} -->",
        re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1) if match else None


def validate_data_row(vault: Path, row: dict[str, str], manuscript_text: str, line: int, errors: list[str]) -> None:
    figure_id = row.get("figure_id", "").strip()
    qa_status = row.get("qa_status", "").strip()
    if qa_status == "not_applicable":
        if not row.get("notes", "").strip():
            errors.append(f"登记表第 {line} 行 not_applicable 缺少理由。")
        return
    required = [
        "claim_id", "source_data_path", "target_journal", "vector_path", "preview_path",
        "grayscale_path", "qa_report_path", "caption_path", "statistics_disclosure", "data_hash",
    ]
    missing = [field for field in required if not row.get(field, "").strip()]
    if missing:
        errors.append(f"数据图 {figure_id} 字段不全：{', '.join(missing)}")
        return
    try:
        source = locate(vault, row["source_data_path"].strip())
        output = locate(vault, row["vector_path"].strip())
        preview = locate(vault, row["preview_path"].strip())
        gray_preview = locate(vault, row["grayscale_path"].strip())
        qa_path = locate(vault, row["qa_report_path"].strip())
        caption_path = locate(vault, row["caption_path"].strip())
    except ValueError as exc:
        errors.append(f"数据图 {figure_id}：{exc}")
        return
    if not source.is_file() or source.suffix.lower() not in {".csv", ".tsv"}:
        errors.append(f"数据图 {figure_id} 的源数据不是可读 CSV/TSV：{source}")
    if not output.is_file() or output.suffix.lower() not in VECTOR_SUFFIXES:
        errors.append(f"数据图 {figure_id} 缺少矢量主输出（SVG/PDF/EPS）：{output}")
    if not preview.is_file() or preview.suffix.lower() != ".png":
        errors.append(f"数据图 {figure_id} 缺少 PNG 预览：{preview}")
    if not gray_preview.is_file() or gray_preview.suffix.lower() != ".png":
        errors.append(f"数据图 {figure_id} 缺少灰度预览：{gray_preview}")
    if not caption_path.is_file():
        errors.append(f"数据图 {figure_id} 缺少图注文件：{caption_path}")
    if any(not path.is_file() for path in (source, output, preview, gray_preview, qa_path, caption_path)):
        return
    try:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"数据图 {figure_id} 的 QA JSON 无法读取：{exc}")
        return
    if qa_status != "passed" or qa.get("status") != "passed":
        errors.append(f"数据图 {figure_id} 未通过独立图件 QA：register={qa_status}, qa={qa.get('status')}")
    if str(qa.get("figure_id", "")) != figure_id:
        errors.append(f"数据图 {figure_id} 的 QA figure_id 不一致。")
    source_record = qa.get("source") or {}
    if str(source_record.get("sha256", "")) != sha256_file(source):
        errors.append(f"数据图 {figure_id} 的源数据哈希与 QA JSON 不一致。")
    if row.get("data_hash", "").strip() != sha256_file(source):
        errors.append(f"数据图 {figure_id} 的登记 data_hash 与源数据不一致。")
    validate_hash(output, artifact_by_path(qa, output), f"数据图 {figure_id} 矢量输出", errors)
    validate_hash(preview, artifact_by_path(qa, preview), f"数据图 {figure_id} PNG 预览", errors)
    validate_hash(gray_preview, artifact_by_path(qa, gray_preview), f"数据图 {figure_id} 灰度预览", errors)
    validate_hash(caption_path, artifact_by_path(qa, caption_path), f"数据图 {figure_id} 图注文件", errors)
    artifacts = qa.get("artifacts") or {}
    existing_artifacts = [Path(str(value.get("path", ""))).expanduser().resolve() for value in artifacts.values() if value.get("path")]
    if not any(path.suffix.lower() in VECTOR_SUFFIXES and path.is_file() for path in existing_artifacts):
        errors.append(f"数据图 {figure_id} 的 QA 产物中没有矢量文件。")
    if not any(path.suffix.lower() == ".png" and path.is_file() for path in existing_artifacts):
        errors.append(f"数据图 {figure_id} 的 QA 产物中没有 PNG。")
    gray = [path for name, value in artifacts.items() if "gray" in name.lower() for path in [Path(str(value.get("path", ""))).expanduser().resolve()]]
    if not gray or not all(path.is_file() for path in gray):
        errors.append(f"数据图 {figure_id} 缺少灰度预览。")
    captions = qa.get("caption_metadata") or {}
    missing_caption = sorted(field for field in CAPTION_FIELDS if not str(captions.get(field, "")).strip())
    if missing_caption:
        errors.append(f"数据图 {figure_id} 的图注统计字段不全：{', '.join(missing_caption)}")
    try:
        disclosed = json.loads(row.get("statistics_disclosure", ""))
    except json.JSONDecodeError:
        errors.append(f"数据图 {figure_id} 的 statistics_disclosure 不是 JSON。")
    else:
        for field in CAPTION_FIELDS:
            if str(disclosed.get(field, "")) != str(captions.get(field, "")):
                errors.append(f"数据图 {figure_id} 的登记统计字段与 QA JSON 不一致：{field}")
    if figure_id not in manuscript_text and f"DATA_FIGURE: {figure_id}" not in manuscript_text:
        errors.append(f"数据图 {figure_id} 未在 Manuscript_Draft.md 中引用或登记稳定标记。")


def validate_mechanism_row(row: dict[str, str], manuscript_text: str, prompts_text: str, line: int, errors: list[str]) -> None:
    figure_id = row.get("figure_id", "").strip()
    prompt_id = row.get("prompt_id", "").strip()
    token = row.get("placeholder_token", "").strip()
    if not row.get("claim_id", "").strip():
        errors.append(f"机制图 {figure_id} 缺少 claim_id。")
    if row.get("qa_status", "").strip() not in {"prompt_ready", "passed"}:
        errors.append(f"机制图 {figure_id} 的 qa_status 必须为 prompt_ready 或 passed。")
    if not prompt_id or not token:
        errors.append(f"机制图 {figure_id} 缺少 prompt_id 或 placeholder_token。")
        return
    expected_token = f"<!-- FIGURE_PLACEHOLDER: {figure_id} -->"
    if token != expected_token:
        errors.append(f"机制图 {figure_id} 的占位标记不是稳定格式：{token}")
    record = prompt_record(prompts_text, prompt_id)
    if record is None:
        errors.append(f"机制图 {figure_id} 无法定位结构化提示词记录 {prompt_id}。")
        return
    if any(marker in record for marker in PLACEHOLDERS):
        errors.append(f"机制图 {figure_id} 的提示词仍含占位符。")
    if f"figure_id: {figure_id}" not in record:
        errors.append(f"机制图 {figure_id} 的提示词记录未反向指向 figure_id。")
    if token not in record:
        errors.append(f"机制图 {figure_id} 的提示词记录未反向指向正文占位。")
    missing_labels = sorted(label for label in PROMPT_LABELS if not re.search(rf"^- {re.escape(label)}:\s*\S", record, re.MULTILINE))
    if missing_labels:
        errors.append(f"机制图 {figure_id} 的详细提示词字段不全：{', '.join(missing_labels)}")
    if "### 详细绘图提示词" not in record:
        errors.append(f"机制图 {figure_id} 缺少完整绘图提示词正文。")
    if token not in manuscript_text or prompt_id.lower() not in manuscript_text.lower():
        errors.append(f"机制图 {figure_id} 的正文占位与提示词文件不能双向定位。")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证独立数据图及机制图提示词阶段。")
    parser.add_argument("--vault", required=True)
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        register = vault / "07_Manuscript" / "figure_register.csv"
        manuscript = vault / "07_Manuscript" / "Manuscript_Draft.md"
        prompts = vault / "07_Manuscript" / "Mechanism_Figure_Prompts.md"
        errors: list[str] = []
        for path in (register, manuscript, prompts):
            if not path.is_file():
                errors.append(f"缺少文件：{path.relative_to(vault)}")
        if errors:
            emit({"status": "invalid", "errors": errors})
            return 1
        with register.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            rows = list(reader)
        missing_fields = REGISTER_FIELDS - fields
        if missing_fields:
            errors.append("图件登记表缺少字段：" + ", ".join(sorted(missing_fields)))
        if not rows:
            errors.append("图件登记表没有记录。")
        manuscript_text = manuscript.read_text(encoding="utf-8")
        prompts_text = prompts.read_text(encoding="utf-8")
        figure_ids = [row.get("figure_id", "").strip() for row in rows if row.get("figure_id", "").strip()]
        prompt_ids = [row.get("prompt_id", "").strip() for row in rows if row.get("prompt_id", "").strip()]
        tokens = [row.get("placeholder_token", "").strip() for row in rows if row.get("placeholder_token", "").strip()]
        for label, values in (("figure_id", figure_ids), ("prompt_id", prompt_ids), ("placeholder_token", tokens)):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                errors.append(f"{label} 不唯一：{', '.join(duplicates)}")
        data_rows = [row for row in rows if row.get("figure_type", "").strip() == "data"]
        mechanism_rows = [row for row in rows if row.get("figure_type", "").strip() == "mechanism"]
        if not data_rows:
            errors.append("缺少 data 记录；若确无数据图，也需登记 qa_status=not_applicable 及理由。")
        if not mechanism_rows:
            errors.append("至少需要一条 mechanism 记录及详细提示词。")
        for line, row in enumerate(rows, start=2):
            figure_id = row.get("figure_id", "").strip()
            figure_type = row.get("figure_type", "").strip()
            if not figure_id or not row.get("claim", "").strip():
                errors.append(f"登记表第 {line} 行缺少 figure_id 或 claim。")
                continue
            if figure_type == "data":
                validate_data_row(vault, row, manuscript_text, line, errors)
            elif figure_type == "mechanism":
                validate_mechanism_row(row, manuscript_text, prompts_text, line, errors)
            else:
                errors.append(f"登记表第 {line} 行 figure_type 必须为 data 或 mechanism。")
        emit({
            "status": "valid" if not errors else "invalid",
            "data_records": len(data_rows),
            "mechanism_records": len(mechanism_rows),
            "errors": errors,
        })
        return 0 if not errors else 1
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
