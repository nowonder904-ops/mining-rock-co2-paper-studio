#!/usr/bin/env python3
"""Offline forward test for the standalone literature and evidence-vault workflow.

The test deliberately runs every production entry point in a subprocess with an
empty CODEX_HOME and dead network proxies.  It uses only fixtures shipped with
this plugin and prints one JSON summary.  Exit code 0 means all checks passed;
exit code 1 means at least one assertion or production command failed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
FIXTURE_ROOT = PLUGIN_ROOT / "assets" / "test-fixtures"

# Import only the plugin-local schema helpers.  This file lives beside the
# module, so no global skill or user-site package is required.
from literature_register import FIELDS, normalize_doi  # noqa: E402


class SelfTestFailure(RuntimeError):
    """Raised for an assertion or subprocess contract failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SelfTestFailure(message)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def parse_json_output(stdout: str, command: list[str]) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise SelfTestFailure(f"命令没有输出 JSON：{' '.join(command)}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SelfTestFailure(
            f"命令输出不是单一 JSON 对象：{' '.join(command)}；输出={text[-1200:]}"
        ) from exc
    if not isinstance(value, dict):
        raise SelfTestFailure(f"命令 JSON 顶层不是对象：{' '.join(command)}")
    return value


def run_script(
    script_name: str,
    arguments: Iterable[str | Path],
    env: dict[str, str],
    expected_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    command = [sys.executable, str(SCRIPT_DIR / script_name), *(str(item) for item in arguments)]
    process = subprocess.run(
        command,
        cwd=PLUGIN_ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    result = parse_json_output(process.stdout, command)
    if process.returncode not in expected_codes:
        raise SelfTestFailure(
            "命令退出码不符合预期："
            f"expected={expected_codes}, actual={process.returncode}, command={' '.join(command)}, "
            f"stdout={process.stdout[-1200:]}, stderr={process.stderr[-1200:]}"
        )
    result["_exit_code"] = process.returncode
    return result


def fixture_record(name: str, paper_id: str, destination: Path) -> Path:
    source = FIXTURE_ROOT / "evidence" / name
    text = source.read_text(encoding="utf-8").replace("{{PAPER_ID}}", paper_id)
    destination.write_text(text, encoding="utf-8", newline="\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="离线验证独立插件的文献、全文闸门、Mode B 与 Obsidian 工作流。")
    parser.add_argument("--keep-temp", action="store_true", help="失败排查时保留临时论文库；默认自动清理")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    current_check = "preflight"
    temp_context: tempfile.TemporaryDirectory[str] | None = None
    temp_root: Path | None = None
    vault: Path | None = None
    failure: dict[str, Any] | None = None

    try:
        required_fixtures = [
            "search/crossref.json",
            "search/openalex.json",
            "search/europepmc.json",
            "search/arxiv.xml",
            "legacy/literature_register.csv",
            "pdf/fake.pdf",
            "pdf/minimal-valid.pdf",
            "evidence/valid-record.json",
            "evidence/invalid-record.json",
        ]
        missing = [item for item in required_fixtures if not (FIXTURE_ROOT / item).is_file()]
        require(not missing, f"缺少自检夹具：{missing}")
        require((FIXTURE_ROOT / "pdf" / "minimal-valid.pdf").stat().st_size >= 512, "最小 PDF 夹具小于 512 字节。")
        checks.append({"name": current_check, "status": "passed", "fixtures": len(required_fixtures)})

        if args.keep_temp:
            temp_root = Path(tempfile.mkdtemp(prefix="mrco2-plugin-self-test-"))
        else:
            temp_context = tempfile.TemporaryDirectory(prefix="mrco2-plugin-self-test-")
            temp_root = Path(temp_context.name)
        isolated_codex_home = temp_root / "empty-codex-home"
        isolated_codex_home.mkdir()

        env = os.environ.copy()
        env.update(
            {
                "CODEX_HOME": str(isolated_codex_home),
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": "",
                # Any accidental network call fails locally instead of reaching the internet.
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "",
            }
        )

        current_check = "initialize-temporary-vault"
        initialized = run_script(
            "init_paper_vault.py",
            ["--parent-dir", temp_root, "--vault-name", "self-test-vault"],
            env,
        )
        require(initialized.get("status") == "created", f"初始化状态异常：{initialized}")
        vault = Path(str(initialized["vault"])).resolve()
        for rel in [
            "00_Control/pipeline_state.yaml",
            "03_Literature/literature_register.csv",
            "03_Literature/Manual_Inbox",
            "04_Reading/Notes",
            "90_Maps",
            "91_Bases",
        ]:
            require((vault / rel).exists(), f"初始化缺少：{rel}")
        initial_state = json.loads((vault / "00_Control" / "pipeline_state.yaml").read_text(encoding="utf-8"))
        require(initial_state.get("schema_version") == 2, "初始化状态不是 schema v2。")
        require(initial_state.get("workflow_architecture") == "standalone", "初始化状态不是 standalone。")
        checks.append({"name": current_check, "status": "passed", "vault_created": True})

        current_check = "legacy-schema-dry-run-and-migration"
        register_path = vault / "03_Literature" / "literature_register.csv"
        legacy_bytes = (FIXTURE_ROOT / "legacy" / "literature_register.csv").read_bytes()
        register_path.write_bytes(legacy_bytes)
        state_path = vault / "00_Control" / "pipeline_state.yaml"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 1
        state["workflow_architecture"] = "skill-dependent"
        write_json(state_path, state)
        manifest_path = vault / "00_Control" / "project_manifest.yaml"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = 1
        manifest["workflow_architecture"] = "skill-dependent"
        write_json(manifest_path, manifest)

        dry_run = run_script("migrate_vault.py", ["--vault", vault], env)
        require(dry_run.get("status") == "dry_run", f"迁移 dry-run 状态异常：{dry_run}")
        changed_types = {item.get("type") for item in dry_run.get("changes", [])}
        require({"csv-schema", "state-schema-v2", "manifest-schema-v2"}.issubset(changed_types), f"dry-run 未识别全部旧 schema：{changed_types}")
        require(register_path.read_bytes() == legacy_bytes, "dry-run 修改了旧登记表。")
        require(not (vault / "00_Control" / "migrations").exists(), "dry-run 不应创建迁移备份。")

        migrated = run_script("migrate_vault.py", ["--vault", vault, "--apply"], env)
        require(migrated.get("status") == "migrated", f"迁移应用状态异常：{migrated}")
        fields_after, rows_after = read_csv(register_path)
        require(fields_after == FIELDS, "迁移后的 literature_register schema 与插件 FIELDS 不一致。")
        require(len(rows_after) == 1 and rows_after[0].get("paper_id") == "P-LEGACY001", "迁移未保留旧文献行。")
        migrated_state = json.loads(state_path.read_text(encoding="utf-8"))
        migrated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(migrated_state.get("schema_version") == 2 and migrated_state.get("workflow_architecture") == "standalone", "状态文件未迁移到 standalone v2。")
        require(migrated_manifest.get("schema_version") == 2 and migrated_manifest.get("workflow_architecture") == "standalone", "manifest 未迁移到 standalone v2。")
        backups = [item for item in (vault / "00_Control" / "migrations").iterdir() if item.is_dir()]
        require(len(backups) == 1, "迁移应创建一个可回滚备份。")
        require((backups[0] / "03_Literature" / "literature_register.csv").read_bytes() == legacy_bytes, "迁移备份未保存原登记表。")
        checks.append({"name": current_check, "status": "passed", "legacy_rows_preserved": 1, "backup_created": True})

        current_check = "scholarly-search-offline-fixtures"
        search = run_script(
            "scholarly_search.py",
            [
                "--vault", vault,
                "--query", "CO2 storage rock mechanics",
                "--from-year", "2024",
                "--to-year", "2026",
                "--sources", "crossref,openalex,europepmc,arxiv",
                "--max-per-source", "10",
                "--include-classics", "0",
                "--fixture-dir", FIXTURE_ROOT / "search",
            ],
            env,
        )
        require(search.get("status") == "ok", f"离线检索未全部成功：{search}")
        require(search.get("candidates") == 4, f"离线检索去重后应为 4 条，实际：{search.get('candidates')}")
        reports = search.get("source_reports", [])
        require(len(reports) == 4 and all(item.get("status") == "ok" for item in reports), f"离线来源报告异常：{reports}")
        candidate_path = Path(str(search["candidate_table"]))
        candidate_fields, candidate_rows = read_csv(candidate_path)
        require(candidate_fields == FIELDS and len(candidate_rows) == 4, "离线候选表 schema 或行数异常。")
        shared_candidate = next(row for row in candidate_rows if normalize_doi(row.get("doi", "")) == "10.1000/shared-fixture")
        require(set(shared_candidate.get("source_provenance", "").split(";")) == {"crossref", "openalex"}, "跨源 DOI 记录未合并 provenance。")
        require(shared_candidate.get("cited_by") == "21", "跨源记录未保留较高被引代理值。")
        for rel in ["search_candidates.bib", "search_candidates.ris", "search_candidates.enw", "search_log.csv"]:
            require((vault / "02_Search" / rel).is_file(), f"离线检索缺少导出：{rel}")
        checks.append({"name": current_check, "status": "passed", "sources": 4, "deduplicated_candidates": 4})

        current_check = "literature-import-and-deduplication"
        first_import = run_script("literature_register.py", ["import-candidates", "--vault", vault, "--input", candidate_path], env)
        require(first_import.get("status") == "imported" and first_import.get("added") == 4, f"首次导入异常：{first_import}")
        second_import = run_script("literature_register.py", ["import-candidates", "--vault", vault, "--input", candidate_path], env)
        require(second_import.get("merged") == 4 and second_import.get("added") == 0, f"重复导入未走 merge：{second_import}")
        doi_variant = run_script(
            "literature_register.py",
            [
                "add", "--vault", vault,
                "--title", "A deliberately different title with the same DOI",
                "--year", "2025",
                "--doi", "https://doi.org/10.1000/shared-fixture",
                "--source-provenance", "self-test",
            ],
            env,
        )
        require(doi_variant.get("status") == "merged", f"DOI URL 规范化未去重：{doi_variant}")
        _, register_rows = read_csv(register_path)
        require(len(register_rows) == 5, f"登记表应为 1 条 legacy + 4 条检索结果，实际 {len(register_rows)}")
        require(sum(normalize_doi(row.get("doi", "")) == "10.1000/shared-fixture" for row in register_rows) == 1, "相同 DOI 产生了重复行。")
        require(any(row.get("paper_id") == "P-LEGACY001" for row in register_rows), "重复导入后 legacy 行丢失。")
        checks.append({"name": current_check, "status": "passed", "final_register_rows": len(register_rows)})

        current_check = "s4.5-fake-and-minimal-pdf-gate"
        valid_row = next(row for row in register_rows if normalize_doi(row.get("doi", "")) == "10.1000/shared-fixture")
        invalid_row = next(row for row in register_rows if normalize_doi(row.get("doi", "")) == "10.2000/rock-fixture")
        valid_id, invalid_id = valid_row["paper_id"], invalid_row["paper_id"]
        for paper_id in [valid_id, invalid_id]:
            updated = run_script(
                "literature_register.py",
                [
                    "update", "--vault", vault, "--paper-id", paper_id,
                    "--set", "screening_status=include",
                    "--set", "fulltext_status=download-failed",
                    "--set", "download_error=offline fixture requires manual recovery",
                ],
                env,
            )
            require(updated.get("status") == "updated", f"未能设置下载失败状态：{updated}")
        stage_state = json.loads(state_path.read_text(encoding="utf-8"))
        stage_state.setdefault("stages", {})["S4"] = "completed"
        stage_state["current_stage"] = "S4"
        write_json(state_path, stage_state)

        gate_build = run_script("download_failure_gate.py", ["build", "--vault", vault], env, expected_codes=(1,))
        require(gate_build.get("status") == "blocked_by_user" and gate_build.get("unresolved_count") == 2, f"S4.5 build 未按预期阻塞：{gate_build}")
        inbox = vault / "03_Literature" / "Manual_Inbox"
        shutil.copyfile(FIXTURE_ROOT / "pdf" / "minimal-valid.pdf", inbox / f"{valid_id}.pdf")
        shutil.copyfile(FIXTURE_ROOT / "pdf" / "fake.pdf", inbox / f"{invalid_id}.pdf")

        first_verify = run_script("download_failure_gate.py", ["verify", "--vault", vault], env, expected_codes=(1,))
        require(first_verify.get("status") == "blocked_by_user", f"首次 PDF 验证不应通过：{first_verify}")
        unresolved = {item.get("paper_id"): item.get("reason", "") for item in first_verify.get("unresolved", [])}
        require(invalid_id in unresolved, "伪 PDF 未被 S4.5 拒绝。")
        require(valid_id in unresolved and "confirm-identity" in unresolved[valid_id], "最小 PDF 结构通过后未要求身份确认。")
        _, failure_rows = read_csv(vault / "03_Literature" / "download_failures.csv")
        failure_by_id = {row["paper_id"]: row for row in failure_rows}
        require(failure_by_id[invalid_id]["status"] == "pending", "伪 PDF 状态应保持 pending。")
        require(failure_by_id[valid_id]["status"] == "downloaded", "结构有效 PDF 应等待 identity confirmation。")
        checks.append({"name": current_check, "status": "passed", "fake_pdf_rejected": True, "minimal_pdf_requires_identity": True})

        current_check = "s4.5-user-resolution-and-resume"
        excluded = run_script(
            "download_failure_gate.py",
            ["exclude", "--vault", vault, "--paper-id", invalid_id, "--reason", "self-test user exclusion after invalid fixture"],
            env,
        )
        require(excluded.get("status") == "exclusion_recorded", f"排除记录失败：{excluded}")
        identity = run_script(
            "download_failure_gate.py",
            ["confirm-identity", "--vault", vault, "--paper-id", valid_id, "--evidence", "fixture title and DOI matched on the simulated first page"],
            env,
        )
        require(identity.get("status") == "identity_confirmed", f"身份确认失败：{identity}")
        final_verify = run_script("download_failure_gate.py", ["verify", "--vault", vault], env)
        require(final_verify.get("status") == "completed" and final_verify.get("next_stage") == "S5", f"S4.5 未恢复到 S5：{final_verify}")
        state_after_gate = json.loads(state_path.read_text(encoding="utf-8"))
        require(state_after_gate.get("stages", {}).get("S4.5") == "completed" and state_after_gate.get("current_stage") == "S5", "S4.5 状态文件未完成恢复。")
        _, register_rows = read_csv(register_path)
        register_by_id = {row["paper_id"]: row for row in register_rows}
        require(register_by_id[valid_id]["fulltext_status"] == "pdf-verified", "有效 PDF 未写回 pdf-verified。")
        require(register_by_id[invalid_id]["screening_status"] == "exclude", "用户排除未写回登记表。")
        checks.append({"name": current_check, "status": "passed", "verified": 1, "excluded": 1})

        current_check = "mode-b-unconfirmed-block-and-user-confirmation"
        prepared_mode_b = run_script("mode_b_manager.py", ["prepare", "--vault", vault], env, expected_codes=(1,))
        require(prepared_mode_b.get("status") == "blocked_by_user" and prepared_mode_b.get("candidate_count") == 1, f"Mode B prepare 未正确阻塞：{prepared_mode_b}")
        invalid_mode_b = run_script("mode_b_manager.py", ["validate", "--vault", vault], env, expected_codes=(1,))
        require(
            invalid_mode_b.get("status") == "invalid"
            and invalid_mode_b.get("config", {}).get("user_confirmed") is False
            and invalid_mode_b.get("count") == 0,
            "未确认 Mode B 未被 validator 拒绝。",
        )
        decisions_input = temp_root / "user-mode-b-decisions.csv"
        with decisions_input.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["paper_id", "confirmed_grade", "order", "note"])
            writer.writeheader()
            writer.writerow({"paper_id": valid_id, "confirmed_grade": "A", "order": "1", "note": "user-confirmed self-test grade"})
        confirmed_mode_b = run_script(
            "mode_b_manager.py",
            [
                "confirm", "--vault", vault, "--decisions", decisions_input,
                "--batch-limit", "1", "--figure-table-level", "relevant",
                "--definitions-mode", "default", "--batch-id", "MODEB-SELF-TEST",
            ],
            env,
        )
        require(confirmed_mode_b.get("status") == "confirmed" and confirmed_mode_b.get("selected") == 1, f"Mode B 用户确认失败：{confirmed_mode_b}")
        valid_mode_b = run_script("mode_b_manager.py", ["validate", "--vault", vault], env)
        require(valid_mode_b.get("status") == "valid" and valid_mode_b.get("count") == 1, f"Mode B 确认后验证失败：{valid_mode_b}")
        checks.append({"name": current_check, "status": "passed", "blocked_before_confirmation": True, "confirmed_selected": 1})

        current_check = "evidence-record-negative-and-positive"
        evidence_prepare = run_script("evidence_vault.py", ["prepare", "--vault", vault], env)
        require(evidence_prepare.get("status") == "prepared", f"证据资产准备失败：{evidence_prepare}")
        invalid_record = fixture_record("invalid-record.json", valid_id, temp_root / "invalid-evidence.json")
        rejected = run_script(
            "evidence_vault.py",
            ["ingest", "--vault", vault, "--paper-id", valid_id, "--record", invalid_record],
            env,
            expected_codes=(1,),
        )
        require(rejected.get("status") == "blocked_by_evidence", f"负例证据记录未被拒绝：{rejected}")
        require(any("source_locator" in item for item in rejected.get("errors", [])), "负例未命中原文定位规则。")
        require(any("limitations" in item for item in rejected.get("errors", [])), "负例未命中证据边界规则。")
        ledger_path = vault / "05_Knowledge" / "Evidence" / "Evidence_Ledger.csv"
        if ledger_path.exists():
            _, rejected_ledger = read_csv(ledger_path)
            require(not any(row.get("paper_id") == valid_id for row in rejected_ledger), "负例证据被写入账本。")

        valid_record = fixture_record("valid-record.json", valid_id, temp_root / "valid-evidence.json")
        ingested = run_script(
            "evidence_vault.py",
            ["ingest", "--vault", vault, "--paper-id", valid_id, "--record", valid_record],
            env,
        )
        require(ingested.get("status") == "ingested" and ingested.get("evidence_count") == 1, f"正例证据写入失败：{ingested}")
        evidence_valid = run_script("evidence_vault.py", ["validate", "--vault", vault], env)
        require(evidence_valid.get("status") == "valid" and evidence_valid.get("evidence_rows") == 1, f"证据知识库验证失败：{evidence_valid}")
        _, ledger_rows = read_csv(ledger_path)
        require(len(ledger_rows) == 1 and ledger_rows[0].get("source_locator") == "p. 3, Fig. 2", "正例证据账本内容异常。")
        checks.append({"name": current_check, "status": "passed", "negative_rejected": True, "positive_ingested": 1})

        current_check = "obsidian-assets-offline-validation"
        obsidian = run_script("validate_obsidian_assets.py", ["--vault", vault], env)
        require(obsidian.get("status") == "valid" and not obsidian.get("errors"), f"Obsidian 离线资产验证失败：{obsidian}")
        require(obsidian.get("obsidian_cli_required") is False, "离线校验不应要求 Obsidian CLI。")
        require((vault / "04_Reading" / "Notes" / f"{valid_id}.md").is_file(), "精读笔记不存在。")
        require((vault / "04_Reading" / "Citation_Cards" / f"{valid_id}.md").is_file(), "引用卡不存在。")
        checks.append({"name": current_check, "status": "passed", "cli_required": False})

        current_check = "global-skill-isolation"
        touched = [str(item.relative_to(isolated_codex_home)) for item in isolated_codex_home.rglob("*")]
        require(not touched, f"自检不应读取或写入全局技能目录；隔离 CODEX_HOME 出现内容：{touched}")
        checks.append({"name": current_check, "status": "passed", "empty_codex_home": True, "network_fixture_mode": True})

    except Exception as exc:  # The summary must remain machine-readable on every failure.
        failure = {
            "check": current_check,
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        checks.append({"name": current_check, "status": "failed", "error": str(exc)})
    finally:
        kept_temp = str(temp_root) if args.keep_temp and temp_root else None
        if temp_context is not None:
            try:
                temp_context.cleanup()
            except OSError as exc:
                if failure is None:
                    failure = {"check": "temporary-cleanup", "type": type(exc).__name__, "message": str(exc)}
                    checks.append({"name": "temporary-cleanup", "status": "failed", "error": str(exc)})

    passed = sum(item.get("status") == "passed" for item in checks)
    failed = sum(item.get("status") == "failed" for item in checks)
    summary: dict[str, Any] = {
        "status": "passed" if failure is None and failed == 0 else "failed",
        "offline": True,
        "isolated_from_global_skills": True,
        "plugin_root": str(PLUGIN_ROOT),
        "python": sys.version.split()[0],
        "summary": {"passed": passed, "failed": failed, "total": len(checks)},
        "checks": checks,
    }
    if args.keep_temp and temp_root:
        summary["kept_temp_root"] = str(temp_root)
    if failure is not None:
        summary["failure"] = failure
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
