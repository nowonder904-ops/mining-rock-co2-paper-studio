#!/usr/bin/env python3
"""Validate the plugin's Markdown, Base, and JSON Canvas subset offline."""

from __future__ import annotations

import argparse
import json
import re

from workflow_common import emit, resolve_vault


REQUIRED_BASES = ["91_Bases/Literature_Dashboard.base", "91_Bases/Mode_B_Status.base", "91_Bases/Evidence_Gaps.base"]
REQUIRED_CANVASES = ["90_Maps/Research_Question_Map.canvas", "90_Maps/Mechanism_Evidence_Map.canvas", "90_Maps/Claim_Citation_Map.canvas"]
REQUIRED_MARKDOWN = ["00_Control/decision_ledger.md", "00_Control/task_board.md", "01_Scoping/RQ_Card.md", "05_Knowledge/Wiki/Index.md", "08_Quality/Evidence_Gaps.md"]
NODE_REQUIRED = {"text": {"id", "type", "text", "x", "y", "width", "height"}, "file": {"id", "type", "file", "x", "y", "width", "height"}, "link": {"id", "type", "url", "x", "y", "width", "height"}, "group": {"id", "type", "label", "x", "y", "width", "height"}}
SIDES = {"top", "right", "bottom", "left"}


def balanced_auto_blocks(text: str) -> bool:
    begins = re.findall(r"<!-- AUTO:([^:]+):BEGIN -->", text)
    ends = re.findall(r"<!-- AUTO:([^:]+):END -->", text)
    return sorted(begins) == sorted(ends) and len(begins) == len(set(begins))


def main() -> int:
    parser = argparse.ArgumentParser(description="离线验证论文库中的 Obsidian 资产。")
    parser.add_argument("--vault", required=True)
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        errors: list[str] = []
        for rel in REQUIRED_BASES:
            path = vault / rel
            if not path.is_file(): errors.append(f"缺少 Base：{rel}"); continue
            text = path.read_text(encoding="utf-8")
            if "\t" in text: errors.append(f"Base 含制表符：{rel}")
            if not re.search(r"(?m)^views:\s*$", text) or not re.search(r"(?m)^\s+- type: (table|cards|list|map)\s*$", text): errors.append(f"Base 缺少合法视图：{rel}")
            if not re.search(r"(?m)^filters:\s*$", text) or not re.search(r"(?m)^properties:\s*$", text): errors.append(f"Base 缺少 filters/properties：{rel}")
            # Keep the check line-local: ``\s`` also consumes newlines and used
            # to join several valid YAML lines into a false positive.
            if re.search(r"(?m)^[ \t]*[^#\s][^:\n]*:[ \t]*[^\n]*:[ \t]*[^\n]*$", text):
                errors.append(f"Base 可能含未引用的冒号值：{rel}")
        for rel in REQUIRED_CANVASES:
            path = vault / rel
            if not path.is_file(): errors.append(f"缺少 Canvas：{rel}"); continue
            try:
                canvas = json.loads(path.read_text(encoding="utf-8"))
                nodes, edges = canvas.get("nodes", []), canvas.get("edges", [])
                if not isinstance(nodes, list) or not isinstance(edges, list): raise ValueError("nodes/edges 必须是数组")
                ids = [item.get("id") for item in [*nodes, *edges]]
                if None in ids or "" in ids or len(ids) != len(set(ids)): errors.append(f"Canvas ID 缺失或重复：{rel}")
                node_ids = {node.get("id") for node in nodes}
                for node in nodes:
                    node_type = node.get("type")
                    if node_type not in NODE_REQUIRED: errors.append(f"Canvas 节点类型无效：{rel}:{node_type}")
                    elif not NODE_REQUIRED[node_type].issubset(node): errors.append(f"Canvas 节点字段不完整：{rel}:{node.get('id')}")
                for edge in edges:
                    if edge.get("fromNode") not in node_ids or edge.get("toNode") not in node_ids: errors.append(f"Canvas 悬空边：{rel}:{edge.get('id')}")
                    for field in ["fromSide", "toSide"]:
                        if field in edge and edge[field] not in SIDES: errors.append(f"Canvas 边方向无效：{rel}:{edge.get('id')}:{field}")
            except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
                errors.append(f"Canvas JSON 无效：{rel}: {exc}")
        for rel in REQUIRED_MARKDOWN:
            path = vault / rel
            if not path.is_file(): errors.append(f"缺少 Markdown：{rel}"); continue
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---\n") or "\n---\n" not in text[4:]: errors.append(f"Markdown properties 未闭合：{rel}")
            if not balanced_auto_blocks(text): errors.append(f"Markdown 自动区块不平衡或重复：{rel}")
        emit({"status": "valid" if not errors else "invalid", "vault": str(vault), "errors": errors, "obsidian_cli_required": False})
        return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
