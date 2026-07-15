#!/usr/bin/env python3
"""Prove that the plugin has one entry skill and no external-skill runtime path."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATTERNS = {
    "global-skill-path": re.compile(r"(?i)(?:CODEX_HOME|\.codex)[/\\]skills"),
    "dependency-bootstrap": re.compile(r"(?i)bootstrap[_-]dependencies|dependency[_-]registry|install[_-]missing"),
    "external-skill-installer": re.compile(r"(?i)skill-installer|codex\s+skill\s+install"),
    "external-executor-handoff": re.compile(r"(?i)target_executor\s*[:=]"),
}


def main() -> int:
    errors = []
    skill_files = list((ROOT / "skills").glob("*/SKILL.md"))
    if len(skill_files) != 1:
        errors.append(f"用户可见 SKILL 数量必须为 1，当前为 {len(skill_files)}。")
    manifest_path = ROOT / "assets" / "capability-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {}; errors.append(f"能力清单无效：{exc}")
    if manifest.get("global_skill_dependencies") != []:
        errors.append("能力清单的 global_skill_dependencies 必须为空。")
    for capability in manifest.get("capabilities", []):
        for rel in capability.get("modules", []):
            if not (ROOT / rel).is_file(): errors.append(f"能力 {capability.get('id')} 缺模块：{rel}")
    ignored = {Path(__file__).resolve()}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path in ignored or "__pycache__" in path.parts: continue
        if path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".base", ".canvas"}: continue
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError: continue
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text): errors.append(f"{label}: {path.relative_to(ROOT)}")
    forbidden_paths = [ROOT / "assets" / "dependency-bundle", ROOT / "assets" / "dependency-registry.json"]
    for path in forbidden_paths:
        if path.exists(): errors.append(f"仍存在依赖捆绑路径：{path.relative_to(ROOT)}")
    caches = [str(path.relative_to(ROOT)) for path in ROOT.rglob("__pycache__") if path.is_dir()]
    if caches: errors.append(f"插件包不应包含 __pycache__：{caches}")
    report = {"status": "valid" if not errors else "invalid", "plugin": str(ROOT), "entry_skills": [str(x.relative_to(ROOT)) for x in skill_files], "capability_count": len(manifest.get("capabilities", [])), "global_skill_dependencies": manifest.get("global_skill_dependencies"), "errors": errors}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
