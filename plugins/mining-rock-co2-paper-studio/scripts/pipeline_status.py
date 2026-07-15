#!/usr/bin/env python3
"""Report and validate the current pipeline state."""

from __future__ import annotations

import argparse

from workflow_common import ALLOWED_STATUSES, STAGES, emit, load_json_yaml, resolve_vault


def main() -> int:
    parser = argparse.ArgumentParser(description="显示论文全流程的断点与阻塞状态。")
    parser.add_argument("--vault", required=True)
    args = parser.parse_args()
    try:
        vault = resolve_vault(args.vault)
        state = load_json_yaml(vault / "00_Control" / "pipeline_state.yaml")
        stages = state.get("stages", {})
        errors = []
        for stage in STAGES:
            value = stages.get(stage)
            if value not in ALLOWED_STATUSES:
                errors.append(f"{stage} 的状态无效或缺失：{value}")
        blocked = [
            stage for stage in STAGES
            if isinstance(stages.get(stage), str) and stages.get(stage, "").startswith("blocked_")
        ]
        next_stage = next((stage for stage in STAGES if stages.get(stage) != "completed"), None)
        emit({
            "status": "valid" if not errors else "invalid",
            "vault": str(vault),
            "schema_version": state.get("schema_version"),
            "workflow_architecture": state.get("workflow_architecture", "legacy-needs-migration"),
            "current_stage": state.get("current_stage"),
            "next_unfinished_stage": next_stage,
            "blocked_stages": blocked,
            "blockers": state.get("blockers", {}),
            "errors": errors,
            "stages": stages,
        })
        return 0 if not errors else 1
    except ValueError as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
