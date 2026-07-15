#!/usr/bin/env python3
"""Extract PDF text with optional local parsers; never treats extraction as identity proof."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_common import emit


def main() -> int:
    parser = argparse.ArgumentParser(description="提取已核验 PDF 的文本；需要 pypdf 或 PyMuPDF。")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-pages", type=int, default=0, help="0 表示全部页")
    args = parser.parse_args()
    try:
        pdf = Path(args.pdf).expanduser().resolve()
        output = Path(args.output).expanduser().resolve()
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf": raise ValueError(f"PDF 不存在：{pdf}")
        pages = []
        engine = ""
        try:
            import pypdf  # type: ignore
            engine = "pypdf"
            reader = pypdf.PdfReader(str(pdf))
            for index, page in enumerate(reader.pages):
                if args.max_pages and index >= args.max_pages: break
                pages.append({"page": index + 1, "text": page.extract_text() or ""})
        except ImportError:
            try:
                import fitz  # type: ignore
                engine = "PyMuPDF"
                document = fitz.open(str(pdf))
                for index, page in enumerate(document):
                    if args.max_pages and index >= args.max_pages: break
                    pages.append({"page": index + 1, "text": page.get_text("text") or ""})
            except ImportError:
                emit({"status": "blocked_by_runtime", "missing": ["pypdf or PyMuPDF"], "pdf": str(pdf)})
                return 1
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {"schema_version": 1, "pdf": str(pdf), "engine": engine, "pages": pages, "warning": "文本提取不等于论文身份核验；页码锚点必须回到原 PDF 复核。"}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        emit({"status": "extracted", "engine": engine, "pages": len(pages), "output": str(output)})
        return 0
    except (OSError, ValueError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
