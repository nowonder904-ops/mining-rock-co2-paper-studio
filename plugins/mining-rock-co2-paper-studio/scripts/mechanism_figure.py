#!/usr/bin/env python3
"""Create evidence-bounded mechanism-figure prompts and manuscript placeholders.

This script never renders a mechanism figure.  It records a detailed prompt,
adds a stable placeholder to the manuscript, and updates the figure register.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


REGISTER_FIELDS = [
    "figure_id", "figure_type", "claim_id", "claim", "source_data_path", "target_journal",
    "vector_path", "output_path", "preview_path", "grayscale_path", "qa_report_path",
    "qa_status", "caption_path", "statistics_disclosure", "prompt_id", "placeholder_token",
    "data_hash", "notes",
]
FIGURE_ID_RE = re.compile(r"^FIG-M[0-9][A-Z0-9._-]*$", re.IGNORECASE)
PROMPT_ID_RE = re.compile(r"^PROMPT-M[0-9][A-Z0-9._-]*$", re.IGNORECASE)
PLACEHOLDER_MARKERS = ("[待填写]", "{{", "TODO", "FIXME")


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        Path(temp_name).replace(path)
    finally:
        temp = Path(temp_name)
        if temp.exists():
            temp.unlink()


def atomic_csv_write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        Path(temp_name).replace(path)
    finally:
        temp = Path(temp_name)
        if temp.exists():
            temp.unlink()


def require_text(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned or any(marker in cleaned for marker in PLACEHOLDER_MARKERS):
        raise ValueError(f"{name} 为空或仍含占位符。")
    return cleaned


def prompt_block(args: argparse.Namespace, placeholder_token: str) -> str:
    evidence = "; ".join(require_text("evidence", item) for item in args.evidence)
    fields = {
        "核心主张": args.claim,
        "核心机制": args.core_mechanism,
        "证据映射": evidence,
        "对象与尺度": args.object_scale,
        "空间布局": args.layout,
        "观察视角": args.view,
        "作用方向与顺序": args.causal_sequence,
        "边界条件": args.boundary_conditions,
        "物理量、单位与符号": args.quantities,
        "分面结构": args.panels,
        "标签、配色与字体": args.style,
        "禁止元素": args.forbidden,
        "图注限制与类比边界": args.limitations,
    }
    for name, value in fields.items():
        require_text(name, value)
    detail = (
        f"绘制一幅面向 {args.target_journal} 的机制示意图，画幅比例 {args.aspect_ratio}，标签语言 {args.language}。"
        f"图只表达一个核心机制：{args.core_mechanism}。对象与尺度：{args.object_scale}。"
        f"采用以下空间布局：{args.layout}；观察视角：{args.view}；分面：{args.panels}。"
        f"仅按证据支持绘制作用方向与过程顺序：{args.causal_sequence}。边界条件：{args.boundary_conditions}。"
        f"标注的物理量、单位和符号：{args.quantities}。标签、配色、线型和字体：{args.style}。"
        f"不得出现：{args.forbidden}。图注必须说明：{args.limitations}。证据锚点：{evidence}。"
    )
    lines = [
        f"<!-- MECHANISM_PROMPT_BEGIN: {args.prompt_id} -->",
        f"## {args.prompt_id}",
        "",
        f"- figure_id: {args.figure_id}",
        f"- claim_id: {args.claim_id}",
        f"- target_journal: {args.target_journal}",
        f"- aspect_ratio: {args.aspect_ratio}",
        f"- language: {args.language}",
    ]
    lines.extend(f"- {name}: {value}" for name, value in fields.items())
    lines.extend([
        f"- 正文占位标记: `{placeholder_token}`",
        "",
        "### 详细绘图提示词",
        "",
        detail,
        "",
        f"<!-- MECHANISM_PROMPT_END: {args.prompt_id} -->",
    ])
    return "\n".join(lines)


def replace_or_append_prompt(text: str, prompt_id: str, block: str, replace: bool) -> str:
    pattern = re.compile(
        rf"<!-- MECHANISM_PROMPT_BEGIN: {re.escape(prompt_id)} -->.*?<!-- MECHANISM_PROMPT_END: {re.escape(prompt_id)} -->",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match and not replace:
        raise ValueError(f"提示词 {prompt_id} 已存在；如需更新请传 --replace。")
    if match:
        return text[:match.start()] + block + text[match.end():]
    header = text.rstrip() if text.strip() else "# 机制图提示词"
    return header + "\n\n" + block + "\n"


def remove_template_prompt(text: str) -> str:
    """Drop the starter placeholder block before recording the first real prompt."""
    if "[待填写]" not in text:
        return text
    frontmatter = ""
    body = text
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end >= 0:
            frontmatter = body[: end + 5].rstrip() + "\n\n"
            body = body[end + 5 :]
    heading = re.search(r"(?m)^#\s+机制图提示词\s*$", body)
    title = heading.group(0) if heading else "# 机制图提示词"
    return frontmatter + title + "\n"


def add_placeholder(text: str, figure_id: str, prompt_id: str, token: str) -> str:
    if token in text:
        return text
    block = f"{token}\n> [机制图占位：{figure_id}；见 Mechanism_Figure_Prompts.md#{prompt_id.lower()}]"
    return text.rstrip() + "\n\n" + block + "\n"


def update_register(path: Path, args: argparse.Namespace, token: str) -> tuple[list[str], list[dict[str, str]]]:
    if path.is_file():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        missing = [field for field in REGISTER_FIELDS if field not in fields]
        if missing:
            raise ValueError("图件登记表字段不完整：" + ", ".join(missing))
    else:
        fields, rows = REGISTER_FIELDS.copy(), []
    duplicate_prompt = [row for row in rows if row.get("prompt_id", "").strip().lower() == args.prompt_id.lower() and row.get("figure_id", "").strip().lower() != args.figure_id.lower()]
    if duplicate_prompt:
        raise ValueError(f"prompt_id 已被其他图件使用：{args.prompt_id}")
    indexes = [index for index, row in enumerate(rows) if row.get("figure_id", "").strip().lower() == args.figure_id.lower()]
    if indexes and not args.replace:
        raise ValueError(f"figure_id 已存在：{args.figure_id}；如需更新请传 --replace。")
    record = {field: "" for field in fields}
    record.update({
        "figure_id": args.figure_id,
        "figure_type": "mechanism",
        "claim_id": args.claim_id,
        "claim": args.claim,
        "target_journal": args.target_journal,
        "qa_status": "prompt_ready",
        "statistics_disclosure": "not_applicable",
        "prompt_id": args.prompt_id,
        "placeholder_token": token,
        "notes": "evidence=" + "; ".join(args.evidence),
    })
    if indexes:
        rows[indexes[0]] = record
    else:
        rows.append(record)
    return fields, rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成机制图提示词、登记记录和正文稳定占位；不绘制图片。")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--figure-id", required=True, help="例如 FIG-M01")
    parser.add_argument("--prompt-id", required=True, help="例如 PROMPT-M01")
    parser.add_argument("--claim", required=True)
    parser.add_argument("--claim-id", required=True)
    parser.add_argument("--target-journal", required=True)
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--language", choices=["zh", "en"], default="zh")
    parser.add_argument("--core-mechanism", required=True)
    parser.add_argument("--evidence", action="append", required=True, help="可重复传入证据账本/文献/数据锚点。")
    parser.add_argument("--object-scale", required=True)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--view", required=True)
    parser.add_argument("--causal-sequence", required=True)
    parser.add_argument("--boundary-conditions", required=True)
    parser.add_argument("--quantities", required=True)
    parser.add_argument("--panels", required=True)
    parser.add_argument("--style", required=True)
    parser.add_argument("--forbidden", required=True)
    parser.add_argument("--limitations", required=True)
    parser.add_argument("--replace", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not FIGURE_ID_RE.fullmatch(args.figure_id):
            raise ValueError("figure_id 必须采用 FIG-M... 稳定格式。")
        if not PROMPT_ID_RE.fullmatch(args.prompt_id):
            raise ValueError("prompt_id 必须采用 PROMPT-M... 稳定格式。")
        vault = Path(args.vault).expanduser().resolve()
        if not vault.is_dir():
            raise ValueError(f"论文库不存在：{vault}")
        manuscript = vault / "07_Manuscript" / "Manuscript_Draft.md"
        prompts = vault / "07_Manuscript" / "Mechanism_Figure_Prompts.md"
        register = vault / "07_Manuscript" / "figure_register.csv"
        if not manuscript.is_file():
            raise ValueError("缺少 Manuscript_Draft.md；机制图占位只能在 S10 正文形成后插入。")
        token = f"<!-- FIGURE_PLACEHOLDER: {args.figure_id} -->"
        block = prompt_block(args, token)
        prompt_text = prompts.read_text(encoding="utf-8") if prompts.is_file() else "# 机制图提示词\n"
        prompt_text = remove_template_prompt(prompt_text)
        new_prompt_text = replace_or_append_prompt(prompt_text, args.prompt_id, block, args.replace)
        manuscript_text = manuscript.read_text(encoding="utf-8")
        new_manuscript_text = add_placeholder(manuscript_text, args.figure_id, args.prompt_id, token)
        fields, rows = update_register(register, args, token)
        atomic_text_write(prompts, new_prompt_text)
        atomic_text_write(manuscript, new_manuscript_text)
        atomic_csv_write(register, fields, rows)
        emit({
            "status": "prompt_ready",
            "figure_id": args.figure_id,
            "prompt_id": args.prompt_id,
            "prompt_file": str(prompts),
            "manuscript": str(manuscript),
            "register": str(register),
            "placeholder_token": token,
            "rendered_image": None,
            "message": "已生成提示词记录与稳定占位；本脚本未生成机制图。",
        })
        return 0
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        emit({"status": "invalid", "code": "mechanism_prompt_failed", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
