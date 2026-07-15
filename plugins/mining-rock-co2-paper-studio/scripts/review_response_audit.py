#!/usr/bin/env python3
"""Audit reviewer-response completeness, traceability, factuality, and readiness."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from workflow_common import atomic_json_write, emit, resolve_vault


COMMENT_ID_RE = re.compile(r"\b(?:E|R\d+)\.\d+\b", re.IGNORECASE)
COMMENT_HEADER_RE = re.compile(
    r"(?im)^(?:#{1,6}\s*)?(?:\*\*)?"
    r"(?:reviewer\s+comment|editor\s+comment|comment|审稿意见|审稿人意见|编辑意见)"
    r"\s+((?:E|R\d+)\.\d+)\b.*$"
)
RESPONSE_MARKER_RE = re.compile(r"(?im)^(?:#{1,6}\s*)?(?:\*\*)?(?:response|author response|回复|作者回复)(?:\*\*)?\s*:?[ \t]*$")
PLACEHOLDER_RE = re.compile(
    r"\{\{[^}\n]+\}\}|\[(?:AUTHOR_INPUT_NEEDED|待填写|待补充|待确认|INSERT[^]]*)\]|"
    r"\b(?:AUTHOR_INPUT_NEEDED|TBD|TODO)\b",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"\b(?:section|page|line|fig(?:ure)?|table|supplement(?:ary)?|abstract|introduction|methods?|results?|discussion|conclusion)"
    r"\s*(?:[A-Z]?\d+(?:\.\d+)*[A-Z]?|[A-Za-z][\w.-]*)?"
    r"|(?:第\s*\d+(?:\.\d+)*\s*节|第\s*\d+\s*页|第\s*\d+\s*行|图\s*[A-Z]?\d+[A-Z]?|表\s*[A-Z]?\d+[A-Z]?|"
    r"摘要|引言|方法|结果|讨论|结论|补充材料)",
    re.IGNORECASE,
)
ACTION_CLAIM_RE = re.compile(
    r"\bwe\s+(?:have\s+)?(?:added|revised|performed|conducted|included|clarified|changed|corrected|removed|expanded)\b"
    r"|\bthe\s+manuscript\s+(?:has\s+been|was)\s+(?:revised|changed|updated)\b"
    r"|我们(?:已|已经)?(?:新增|补充|修改|修订|进行了|开展了|澄清|更正|删除|扩展)",
    re.IGNORECASE,
)
EVIDENCE_ACTION_RE = re.compile(
    r"\b(?:experiment|analysis|simulation|test|validation|statistics?)\b|试验|实验|分析|模拟|验证|统计",
    re.IGNORECASE,
)
RESULT_SIGNAL_RE = re.compile(
    r"\b(?:result(?:s)?|show(?:ed|s)?|found|observed|yielded|increased|decreased|no\s+significant)\b"
    r"|结果|表明|显示|发现|观察到|提高|降低|无显著",
    re.IGNORECASE,
)
BLOCKING_TOPIC_RE = re.compile(r"ethic|consent|integrity|approval|compliance|伦理|知情同意|完整性|批准|合规", re.IGNORECASE)
ALLOWED_ACTIONS = {
    "ACCEPT_TEXT",
    "ACCEPT_ANALYSIS",
    "ACCEPT_EXPERIMENT",
    "ACCEPT_FIGURE",
    "CLARIFY_EXISTING",
    "ADD_CITATION",
    "SOFTEN_CLAIM",
    "PARTIAL",
    "DISAGREE",
    "OUT_OF_SCOPE",
    "AUTHOR_INPUT_NEEDED",
    "BLOCKING",
}
ALLOWED_READINESS = {"ready_to_submit", "draft_with_placeholders", "needs_author_input", "blocked"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_comment(text: str) -> str:
    value = re.sub(r"[*_`>#]", " ", text)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def parse_response_sections(text: str) -> list[dict[str, Any]]:
    matches = list(COMMENT_HEADER_RE.finditer(text))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.end() : end]
        response_match = RESPONSE_MARKER_RE.search(chunk)
        if response_match:
            comment = chunk[: response_match.start()].strip()
            response = chunk[response_match.end() :].strip()
        else:
            comment = chunk.strip()
            response = ""
        sections.append(
            {
                "comment_id": match.group(1).upper(),
                "header": match.group(0).strip(),
                "comment": comment,
                "response": response,
                "start_line": text.count("\n", 0, match.start()) + 1,
            }
        )
    return sections


def parse_source_comments(text: str) -> dict[str, str]:
    structured = parse_response_sections(text)
    if structured:
        return {item["comment_id"]: item["comment"] for item in structured}
    result: dict[str, str] = {}
    matches = list(COMMENT_ID_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(0).upper()] = text[match.end() : end].strip()
    return result


def load_tracker(path: Path | None) -> tuple[dict[str, dict[str, str]], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if path is None or not path.is_file():
        return {}, errors, ["tracker_not_supplied"]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    required = {"comment_id", "severity", "category", "action", "readiness", "manuscript_location"}
    missing_fields = sorted(required - set(fields))
    if missing_fields:
        errors.append(f"tracker_missing_columns:{','.join(missing_fields)}")
    tracker: dict[str, dict[str, str]] = {}
    for line_number, row in enumerate(rows, start=2):
        comment_id = (row.get("comment_id") or "").strip().upper()
        if not COMMENT_ID_RE.fullmatch(comment_id):
            errors.append(f"tracker_invalid_id:line-{line_number}:{comment_id or 'empty'}")
            continue
        if comment_id in tracker:
            errors.append(f"tracker_duplicate_id:{comment_id}")
        tracker[comment_id] = {key: (value or "").strip() for key, value in row.items() if key is not None}
        action = tracker[comment_id].get("action", "").upper()
        readiness = tracker[comment_id].get("readiness", "").lower()
        if action not in ALLOWED_ACTIONS:
            errors.append(f"tracker_invalid_action:{comment_id}:{action or 'empty'}")
        if readiness not in ALLOWED_READINESS:
            errors.append(f"tracker_invalid_readiness:{comment_id}:{readiness or 'empty'}")
        if readiness == "ready_to_submit":
            for field in ("action", "manuscript_location"):
                if not tracker[comment_id].get(field, ""):
                    errors.append(f"tracker_ready_missing_field:{comment_id}:{field}")
            if action in {"ACCEPT_ANALYSIS", "ACCEPT_EXPERIMENT", "ACCEPT_FIGURE", "ADD_CITATION"} and not tracker[comment_id].get("evidence", ""):
                errors.append(f"tracker_ready_missing_evidence:{comment_id}")
    if not rows:
        errors.append("tracker_empty")
    return tracker, errors, warnings


def audit(args: argparse.Namespace) -> int:
    vault = resolve_vault(args.vault)
    response_path = Path(args.response).expanduser().resolve() if args.response else vault / "10_Revision" / "Reviewer_Response.md"
    if not response_path.is_file():
        raise ValueError(f"response_not_found:{response_path}")
    response_text = response_path.read_text(encoding="utf-8-sig")
    sections = parse_response_sections(response_text)
    errors: list[str] = []
    warnings: list[str] = []
    if not sections:
        errors.append("no_structured_comment_sections")
    ids = [item["comment_id"] for item in sections]
    for comment_id, count in Counter(ids).items():
        if count > 1:
            errors.append(f"duplicate_comment_id:{comment_id}")

    tracker_path: Path | None
    if args.tracker:
        tracker_path = Path(args.tracker).expanduser().resolve()
    else:
        candidate = vault / "10_Revision" / "response_tracker.csv"
        tracker_path = candidate if candidate.is_file() else None
    tracker, tracker_errors, tracker_warnings = load_tracker(tracker_path)
    errors.extend(tracker_errors)
    warnings.extend(tracker_warnings)

    source_comments: dict[str, str] = {}
    source_path: Path | None = None
    if args.source_comments:
        source_path = Path(args.source_comments).expanduser().resolve()
        if not source_path.is_file():
            raise ValueError(f"source_comments_not_found:{source_path}")
        source_comments = parse_source_comments(source_path.read_text(encoding="utf-8-sig"))
        missing = sorted(set(source_comments) - set(ids))
        unknown = sorted(set(ids) - set(source_comments))
        if missing:
            errors.append(f"source_comments_unanswered:{','.join(missing)}")
        if unknown:
            warnings.append(f"response_ids_not_in_source:{','.join(unknown)}")

    details: list[dict[str, Any]] = []
    blocking_missing_input = False
    for item in sections:
        comment_id = item["comment_id"]
        comment = item["comment"]
        response = item["response"]
        item_errors: list[str] = []
        item_warnings: list[str] = []
        if len(normalize_comment(comment)) < 4:
            item_errors.append("comment_text_missing")
        if len(normalize_comment(response)) < 8:
            item_errors.append("response_missing_or_too_short")
        placeholders = [match.group(0) for match in PLACEHOLDER_RE.finditer(response)]
        if placeholders:
            item_errors.append("visible_placeholder")
        locations = sorted(set(match.group(0).strip() for match in LOCATION_RE.finditer(response)))
        action_claim = bool(ACTION_CLAIM_RE.search(response))
        if action_claim and not locations:
            item_errors.append("claimed_change_without_location")
        evidence_action = action_claim and bool(EVIDENCE_ACTION_RE.search(response))
        tracker_evidence = tracker.get(comment_id, {}).get("evidence", "")
        if evidence_action and not (RESULT_SIGNAL_RE.search(response) or tracker_evidence):
            item_errors.append("claimed_analysis_or_experiment_without_result_evidence")
        if tracker and comment_id not in tracker:
            item_errors.append("tracker_entry_missing")
        if comment_id in tracker:
            row = tracker[comment_id]
            tracker_location = row.get("manuscript_location", "")
            if row.get("readiness", "").lower() == "ready_to_submit" and not (locations or tracker_location):
                item_errors.append("ready_without_traceable_location")
            if row.get("action", "").upper() in {"AUTHOR_INPUT_NEEDED", "BLOCKING"}:
                blocking_missing_input = blocking_missing_input or bool(BLOCKING_TOPIC_RE.search(comment + " " + response))
        similarity: float | None = None
        if comment_id in source_comments:
            source_comment = normalize_comment(source_comments[comment_id])
            packaged_comment = normalize_comment(comment)
            similarity = SequenceMatcher(None, source_comment, packaged_comment).ratio() if source_comment and packaged_comment else 0.0
            if similarity < args.comment_similarity:
                item_errors.append(f"comment_not_preserved:{similarity:.3f}")
        if re.search(r"\bline\s+\d+\b|第\s*\d+\s*行", response, flags=re.IGNORECASE) and not args.manuscript:
            item_warnings.append("line_number_not_cross_checked_without_manuscript")
        if placeholders and BLOCKING_TOPIC_RE.search(comment + " " + response):
            blocking_missing_input = True
        errors.extend(f"{comment_id}:{value}" for value in item_errors)
        warnings.extend(f"{comment_id}:{value}" for value in item_warnings)
        details.append(
            {
                "comment_id": comment_id,
                "start_line": item["start_line"],
                "comment_characters": len(comment),
                "response_characters": len(response),
                "locations": locations,
                "action_claim": action_claim,
                "evidence_action": evidence_action,
                "placeholders": placeholders,
                "source_similarity": similarity,
                "errors": item_errors,
                "warnings": item_warnings,
            }
        )

    if tracker:
        extra_tracker = sorted(set(tracker) - set(ids))
        if extra_tracker:
            warnings.append(f"tracker_ids_without_response_section:{','.join(extra_tracker)}")
    package_placeholders = sorted(set(match.group(0) for match in PLACEHOLDER_RE.finditer(response_text)))
    if blocking_missing_input:
        readiness = "blocked"
    elif package_placeholders:
        readiness = "needs_author_input"
    elif errors:
        readiness = "draft_with_placeholders"
    else:
        readiness = "ready_to_submit"

    manuscript_cross_check: dict[str, Any] = {"performed": False}
    if args.manuscript:
        manuscript_path = Path(args.manuscript).expanduser().resolve()
        if not manuscript_path.is_file():
            raise ValueError(f"manuscript_not_found:{manuscript_path}")
        manuscript_text = manuscript_path.read_text(encoding="utf-8-sig")
        referenced_tokens = sorted(set(token for detail in details for token in detail["locations"]))
        absent_tokens = [token for token in referenced_tokens if normalize_comment(token) not in normalize_comment(manuscript_text)]
        if absent_tokens:
            warnings.append(f"location_tokens_not_found_verbatim:{'|'.join(absent_tokens[:20])}")
        manuscript_cross_check = {
            "performed": True,
            "path": str(manuscript_path),
            "referenced_location_tokens": referenced_tokens,
            "tokens_not_found_verbatim": absent_tokens,
        }

    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "valid" if readiness == "ready_to_submit" else "invalid",
        "package_readiness": readiness,
        "vault": str(vault),
        "response": str(response_path),
        "source_comments": str(source_path) if source_path else None,
        "tracker": str(tracker_path) if tracker_path else None,
        "comment_count": len(sections),
        "comments": details,
        "manuscript_cross_check": manuscript_cross_check,
        "package_placeholders": package_placeholders,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }
    destination = Path(args.output).expanduser().resolve() if args.output else vault / "10_Revision" / "Reviewer_Response_Audit.json"
    if not args.check_only:
        atomic_json_write(destination, report)
    payload = dict(report)
    payload["written"] = None if args.check_only else str(destination)
    emit(payload)
    return 0 if report["status"] == "valid" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="审计逐点回复的覆盖、追溯、事实和提交就绪状态。")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--response")
    parser.add_argument("--source-comments")
    parser.add_argument("--tracker")
    parser.add_argument("--manuscript")
    parser.add_argument("--comment-similarity", type=float, default=0.90)
    parser.add_argument("--output")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        if not 0.0 <= args.comment_similarity <= 1.0:
            raise ValueError("comment_similarity_must_be_between_0_and_1")
        return audit(args)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        emit({"status": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
