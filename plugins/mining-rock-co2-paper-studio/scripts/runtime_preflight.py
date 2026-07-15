#!/usr/bin/env python3
"""Report runtime capabilities without checking or installing global skills."""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path


def module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def command(*names: str) -> str:
    for name in names:
        value = shutil.which(name)
        if value:
            return value
    return ""


def windows_word() -> str:
    candidates = [
        Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"),
        Path(r"C:\Program Files (x86)\Microsoft Office\root\Office16\WINWORD.EXE"),
    ]
    return next((str(path) for path in candidates if path.is_file()), "")


def probe_network(url: str, timeout: float) -> tuple[bool, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "mrco2-paper-studio/0.2"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return 200 <= response.status < 500, f"HTTP {response.status}"
    except Exception as exc:  # network failures are reported, never hidden
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查独立论文插件所需的运行环境；不安装任何软件或 SKILL。")
    parser.add_argument("--probe-network", action="store_true", help="实际请求 Crossref 以检查联网能力")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--output", help="可选 JSON 报告路径")
    args = parser.parse_args()

    python_ok = sys.version_info >= (3, 10)
    matplotlib_ok = module("matplotlib")
    pandas_ok = module("pandas")
    numpy_ok = module("numpy")
    pillow_ok = module("PIL")
    pdf_reader = "pypdf" if module("pypdf") else ("fitz" if module("fitz") else "")
    pandoc = command("pandoc")
    tex = command("xelatex", "pdflatex", "latexmk")
    word = windows_word() if sys.platform == "win32" else ""
    obsidian = command("obsidian")
    network_ok, network_detail = (None, "not probed")
    if args.probe_network:
        network_ok, network_detail = probe_network("https://api.crossref.org/works?rows=0", args.timeout)

    capabilities = {
        "core_state_and_vault": python_ok,
        "scholarly_search": bool(python_ok and (network_ok is not False)),
        "pdf_structural_validation": python_ok,
        "pdf_text_extraction": bool(pdf_reader),
        "publication_figures": bool(matplotlib_ok and numpy_ok),
        "tabular_eda_extended": bool(pandas_ok and numpy_ok),
        "png_qa": pillow_ok,
        "docx_export": python_ok,
        "latex_export": python_ok,
        "pdf_export": bool((pandoc and tex) or word),
        "obsidian_cli_optional": bool(obsidian),
    }
    blockers = []
    if not python_ok:
        blockers.append("Python 3.10+ is required for the plugin's deterministic core.")
    report = {
        "status": "ready_core" if python_ok else "blocked_by_runtime",
        "standalone": True,
        "global_skill_dependencies": [],
        "python": {"version": platform.python_version(), "executable": sys.executable, "ok": python_ok},
        "modules": {"matplotlib": matplotlib_ok, "pandas": pandas_ok, "numpy": numpy_ok, "Pillow": pillow_ok, "pdf_reader": pdf_reader},
        "commands": {"pandoc": pandoc, "tex": tex, "word": word, "obsidian": obsidian},
        "network": {"probed": args.probe_network, "ok": network_ok, "detail": network_detail},
        "capabilities": capabilities,
        "blockers": blockers,
        "notes": [
            "缺少可选能力只阻塞对应阶段，不能把降级产物标记为完整交付。",
            "本脚本不会安装 Python 包、系统程序、MCP、浏览器扩展、凭据或其他 SKILL。",
        ],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if python_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
