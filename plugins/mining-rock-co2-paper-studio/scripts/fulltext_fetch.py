#!/usr/bin/env python3
"""Download only lawfully reachable PDFs and route every failure into S4.5."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from literature_register import FIELDS, load_rows, save_rows
from workflow_common import emit, resolve_vault


FORBIDDEN_HOST_FRAGMENTS = {"sci-hub", "libgen", "library-genesis", "z-lib"}
DOMAIN_DIR = {
    "mining-engineering": "Mining_Engineering", "rock-mechanics": "Rock_Mechanics",
    "co2-storage": "CO2_Storage", "cross-domain": "Cross_Domain",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"只允许 http/https 全文链接：{value}")
    host = parsed.hostname.casefold()
    if any(fragment in host for fragment in FORBIDDEN_HOST_FRAGMENTS):
        raise ValueError(f"拒绝访问绕过访问控制的来源：{host}")
    return value


def inspect_pdf(path: Path) -> tuple[bool, str, str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(5)
            handle.seek(max(0, size - 4096))
            tail = handle.read()
        if size < 512: return False, "file-too-small", ""
        if head != b"%PDF-": return False, "missing-pdf-signature", ""
        if b"%%EOF" not in tail: return False, "missing-pdf-eof", ""
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return True, "valid-pdf-payload", digest
    except OSError as exc:
        return False, f"io-error:{exc}", ""


def classify_html(payload: bytes) -> str:
    text = payload[:200000].decode("utf-8", errors="ignore").casefold()
    if "captcha" in text or "verify you are human" in text: return "captcha"
    if any(token in text for token in ["sign in", "log in", "institutional access", "subscribe to access"]): return "login-required"
    return "non-pdf-response"


def fetch(url: str, timeout: float, cache_path: Path) -> tuple[str, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "mining-rock-co2-paper-studio/0.2", "Accept": "application/pdf,text/html;q=0.5,*/*;q=0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "").lower()
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}: return "paywalled", f"HTTP {exc.code}", ""
        if exc.code == 429: return "captcha", "HTTP 429/rate-limited", ""
        return "download-failed", f"HTTP {exc.code}", ""
    except (urllib.error.URLError, TimeoutError) as exc:
        return "download-failed", f"{type(exc).__name__}: {exc}", ""

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if payload.startswith(b"%PDF-") or "application/pdf" in content_type:
        cache_path.write_bytes(payload)
        valid, detail, digest = inspect_pdf(cache_path)
        return ("pdf-available", detail, digest) if valid else ("download-failed", detail, "")
    html_path = cache_path.with_suffix(".html")
    html_path.write_bytes(payload)
    return classify_html(payload), f"content-type={content_type or 'unknown'}; cached={html_path}", ""


def main() -> int:
    parser = argparse.ArgumentParser(description="尝试下载合法可访问全文；失败项自动进入人工补全文闸门。")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--paper-id", action="append", help="只处理指定 paper_id，可重复")
    parser.add_argument("--include-source-url", action="store_true", help="OA 链接为空时也尝试普通落地页；默认只尝试 oa_url")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--build-gate", action="store_true", help="仅在 S4 已标记 completed 后立即生成 S4.5 清单")
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        register = vault / "03_Literature" / "literature_register.csv"
        rows = load_rows(register)
        selected = set(args.paper_id or [])
        if selected:
            missing = selected - {row.get("paper_id", "") for row in rows}
            if missing: raise ValueError(f"找不到 paper_id：{', '.join(sorted(missing))}")
        results = []
        cache_root = vault / "03_Literature" / "Download_Cache"
        for row in rows:
            paper_id = row.get("paper_id", "")
            if selected and paper_id not in selected: continue
            if row.get("screening_status") == "exclude" or row.get("fulltext_status") == "pdf-verified": continue
            candidates = [row.get("oa_url", "")]
            if args.include_source_url: candidates.append(row.get("source_url", ""))
            candidates = list(dict.fromkeys(filter(None, (safe_url(x) for x in candidates))))
            if not candidates:
                row.update({"fulltext_status": "manual-download-required", "download_error": "no-lawful-direct-pdf-url", "blocker": "S4.5-manual-fulltext", "last_transition_at": now_iso()})
                results.append({"paper_id": paper_id, "status": "manual-download-required", "detail": "no lawful direct PDF URL"})
                continue
            final_status, detail, digest = "download-failed", "all candidates failed", ""
            temp_pdf = cache_root / f"{paper_id}.part.pdf"
            for url in candidates:
                status, attempt_detail, attempt_digest = fetch(url, args.timeout, temp_pdf)
                final_status, detail, digest = status, f"{url}: {attempt_detail}", attempt_digest
                if status == "pdf-available": break
                if status in {"paywalled", "login-required", "captcha"}: break
            if final_status == "pdf-available":
                folder = DOMAIN_DIR.get(row.get("domain", ""), "Cross_Domain")
                destination = vault / "03_Literature" / "PDF" / folder / f"{paper_id}.pdf"
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    old_valid, _, old_digest = inspect_pdf(destination)
                    if not old_valid or old_digest != digest:
                        final_status, detail = "download-failed", "destination-exists-with-different-or-invalid-content"
                    else:
                        temp_pdf.unlink(missing_ok=True)
                else:
                    temp_pdf.replace(destination)
                if final_status == "pdf-available":
                    row.update({"fulltext_status": "pdf-available", "pdf_path": str(destination.relative_to(vault)), "content_hash": digest, "download_error": "", "blocker": "", "last_transition_at": now_iso()})
            if final_status != "pdf-available":
                temp_pdf.unlink(missing_ok=True)
                mapped = final_status if final_status in {"paywalled", "login-required", "captcha"} else "download-failed"
                row.update({"fulltext_status": mapped, "download_error": detail, "blocker": "S4.5-manual-fulltext", "last_transition_at": now_iso()})
            results.append({"paper_id": paper_id, "status": final_status, "detail": detail, "sha256": digest})
        save_rows(register, rows)
        failures = [x for x in results if x["status"] != "pdf-available"]
        gate_report = None
        if args.build_gate:
            gate_script = Path(__file__).resolve().parent / "download_failure_gate.py"
            process = subprocess.run([sys.executable, str(gate_script), "build", "--vault", str(vault)], text=True, capture_output=True, encoding="utf-8", errors="replace", check=False)
            try: gate_report = json.loads(process.stdout)
            except json.JSONDecodeError: gate_report = {"status": "error", "stdout": process.stdout, "stderr": process.stderr}
        status = "blocked_by_user" if failures and gate_report and gate_report.get("status") == "blocked_by_user" else ("failures_pending_gate" if failures else "download_attempts_complete")
        emit({"status": status, "processed": len(results), "failures": failures, "results": results, "gate": gate_report, "requires_gate_build": bool(failures and not args.build_gate)})
        return 1 if failures else 0
    except (OSError, ValueError) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
