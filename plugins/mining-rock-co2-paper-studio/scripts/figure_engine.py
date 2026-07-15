#!/usr/bin/env python3
"""Standalone deterministic data-figure engine for the paper workflow.

The module deliberately depends on no Codex skill.  Profiling uses only the
Python standard library.  Plotting is optional and reports ``blocked_by_runtime``
when matplotlib or Pillow is unavailable instead of pretending that a figure
was produced.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import statistics
import sys
import tempfile
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2
EXIT_BLOCKED = 3
MISSING_MARKERS = {"", "na", "n/a", "nan", "null", "none", "-"}
VECTOR_SUFFIXES = {".svg", ".pdf"}
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]


class EngineFailure(Exception):
    def __init__(self, status: str, code: str, message: str, *, details: Any = None, exit_code: int = EXIT_INVALID):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.exit_code = exit_code


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temp_name).replace(path)
    finally:
        temp = Path(temp_name)
        if temp.exists():
            temp.unlink()


def decode_table(raw: bytes, label: str) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise EngineFailure("invalid_data", "unsupported_encoding", f"表格不是 UTF-8/UTF-8-BOM：{label}")


def choose_delimiter(label: str, text: str, requested: str) -> str:
    if requested == "comma":
        return ","
    if requested == "tab":
        return "\t"
    suffix = Path(label).suffix.lower()
    if suffix == ".tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(text[:8192], delimiters=",\t").delimiter
    except csv.Error:
        return "\t" if text.count("\t") > text.count(",") else ","


def load_table(source: str, delimiter: str = "auto") -> dict[str, Any]:
    if source == "-":
        raw = sys.stdin.buffer.read()
        label = "<stdin>"
        source_path = None
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise EngineFailure("invalid_data", "source_missing", f"源数据不存在：{path}")
        if path.suffix.lower() not in {".csv", ".tsv"}:
            raise EngineFailure("invalid_data", "unsupported_source_type", "独立图件引擎只接受 CSV/TSV；请先把工作簿导出为 CSV。")
        raw = path.read_bytes()
        label = str(path)
        source_path = path
    if not raw:
        raise EngineFailure("invalid_data", "source_empty", f"源数据为空：{label}")
    text = decode_table(raw, label)
    actual_delimiter = choose_delimiter(label, text, delimiter)
    reader = csv.DictReader(io.StringIO(text), delimiter=actual_delimiter)
    raw_fields = reader.fieldnames or []
    fields = [field.strip() if field else "" for field in raw_fields]
    if not fields or any(not field for field in fields):
        raise EngineFailure("invalid_data", "invalid_header", "CSV/TSV 必须有非空表头。")
    duplicates = sorted({field for field in fields if fields.count(field) > 1})
    if duplicates:
        raise EngineFailure("invalid_data", "duplicate_columns", "存在重复字段名。", details=duplicates)
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        row = {fields[index]: (raw_row.get(raw_fields[index]) or "").strip() for index in range(len(fields))}
        if any(value for value in row.values()):
            rows.append(row)
    if not rows:
        raise EngineFailure("invalid_data", "no_data_rows", "表格没有数据行。")
    return {
        "label": label,
        "path": source_path,
        "sha256": sha256_bytes(raw),
        "delimiter": "tab" if actual_delimiter == "\t" else "comma",
        "fields": fields,
        "rows": rows,
    }


def is_missing(value: str) -> bool:
    return value.strip().lower() in MISSING_MARKERS


def to_float(value: str) -> float | None:
    if is_missing(value):
        return None
    try:
        result = float(value.replace(" ", ""))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def looks_datetime(value: str) -> bool:
    if is_missing(value):
        return False
    candidate = value.strip().replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        return False


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denom_x = sum((x - mean_x) ** 2 for x in xs)
    denom_y = sum((y - mean_y) ** 2 for y in ys)
    denominator = math.sqrt(denom_x * denom_y)
    return numerator / denominator if denominator else None


def profile_column(name: str, values: list[str]) -> dict[str, Any]:
    nonmissing = [value for value in values if not is_missing(value)]
    numeric = [to_float(value) for value in nonmissing]
    numeric_values = [value for value in numeric if value is not None]
    datetime_count = sum(1 for value in nonmissing if looks_datetime(value))
    if nonmissing and len(numeric_values) == len(nonmissing):
        kind = "numeric"
    elif nonmissing and datetime_count == len(nonmissing):
        kind = "datetime"
    else:
        kind = "categorical"
    result: dict[str, Any] = {
        "name": name,
        "type": kind,
        "rows": len(values),
        "nonmissing": len(nonmissing),
        "missing": len(values) - len(nonmissing),
        "missing_rate": round((len(values) - len(nonmissing)) / len(values), 6),
        "unique": len(set(nonmissing)),
    }
    if kind == "numeric" and numeric_values:
        q1 = percentile(numeric_values, 0.25)
        q3 = percentile(numeric_values, 0.75)
        iqr = q3 - q1
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        mean = statistics.fmean(numeric_values)
        stdev = statistics.stdev(numeric_values) if len(numeric_values) > 1 else 0.0
        skew = 0.0
        if stdev and len(numeric_values) > 2:
            skew = sum(((value - mean) / stdev) ** 3 for value in numeric_values) / len(numeric_values)
        positives = [value for value in numeric_values if value > 0]
        result.update({
            "min": min(numeric_values), "q1": q1, "median": statistics.median(numeric_values),
            "mean": mean, "q3": q3, "max": max(numeric_values), "stdev": stdev,
            "skewness": skew, "iqr_outliers": sum(value < lower or value > upper for value in numeric_values),
            "suggest_log_axis": bool(len(positives) == len(numeric_values) and min(positives) > 0 and max(positives) / min(positives) >= 1000),
        })
    elif kind == "categorical":
        counts = Counter(nonmissing)
        result["top_values"] = [{"value": key, "count": count} for key, count in counts.most_common(12)]
    elif kind == "datetime" and nonmissing:
        result["min"] = min(nonmissing)
        result["max"] = max(nonmissing)
    return result


def chart_suggestions(profile: dict[str, Any], group_columns: list[str]) -> list[dict[str, str]]:
    columns = profile["columns"]
    numeric = [column["name"] for column in columns if column["type"] == "numeric"]
    categorical = [column["name"] for column in columns if column["type"] == "categorical"]
    datetimes = [column["name"] for column in columns if column["type"] == "datetime"]
    suggestions: list[dict[str, str]] = []
    if datetimes and numeric:
        suggestions.append({"chart": "line", "reason": f"{datetimes[0]} 为时间字段，{numeric[0]} 为连续结果；适合展示趋势。"})
    if len(numeric) >= 2:
        suggestions.append({"chart": "scatter", "reason": f"{numeric[0]} 与 {numeric[1]} 均为连续变量；适合检验关系与离群点。"})
    if group_columns and numeric:
        suggestions.append({"chart": "box", "reason": "存在明确分组与连续结果；箱线图叠加原始点可展示分布和样本量。"})
    elif categorical and numeric:
        suggestions.append({"chart": "box", "reason": f"{categorical[0]} 为类别、{numeric[0]} 为连续结果；优先展示组内分布。"})
    if numeric:
        suggestions.append({"chart": "hist", "reason": f"直方图适合检查 {numeric[0]} 的分布、偏态和异常值。"})
    if len(numeric) >= 3:
        suggestions.append({"chart": "heatmap", "reason": "至少三个连续变量；相关矩阵热力图适合总览变量关系。"})
    if categorical:
        suggestions.append({"chart": "bar", "reason": f"横向/纵向柱可展示 {categorical[0]} 的计数；连续结果的小样本均值柱会被拦截。"})
    return suggestions


def profile_table(table: dict[str, Any], group_columns: list[str]) -> dict[str, Any]:
    missing_groups = [name for name in group_columns if name not in table["fields"]]
    if missing_groups:
        raise EngineFailure("invalid_data", "group_columns_missing", "分组字段不存在。", details=missing_groups)
    columns = [profile_column(name, [row[name] for row in table["rows"]]) for name in table["fields"]]
    numeric_names = [column["name"] for column in columns if column["type"] == "numeric"]
    correlations: list[dict[str, Any]] = []
    for index, left in enumerate(numeric_names):
        for right in numeric_names[index + 1:]:
            pairs = [(to_float(row[left]), to_float(row[right])) for row in table["rows"]]
            valid = [(x, y) for x, y in pairs if x is not None and y is not None]
            value = pearson([x for x, _ in valid], [y for _, y in valid]) if valid else None
            correlations.append({"x": left, "y": right, "n": len(valid), "pearson_r": value})
    group_counts: list[dict[str, Any]] = []
    if group_columns:
        counts = Counter(tuple(row[name] for name in group_columns) for row in table["rows"])
        group_counts = [{"group": dict(zip(group_columns, key)), "n": count} for key, count in sorted(counts.items())]
    result = {
        "schema_version": 1,
        "status": "profiled",
        "source": {"path": table["label"], "sha256": table["sha256"], "delimiter": table["delimiter"]},
        "row_count": len(table["rows"]),
        "column_count": len(table["fields"]),
        "columns": columns,
        "group_counts": group_counts,
        "correlations": correlations,
    }
    result["suggestions"] = chart_suggestions(result, group_columns)
    return result


def require_plot_runtime() -> tuple[Any, Any, Any]:
    missing: list[str] = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.font_manager as font_manager
        import matplotlib.pyplot as pyplot
    except (ImportError, ModuleNotFoundError):
        matplotlib = font_manager = pyplot = None
        missing.append("matplotlib")
    try:
        from PIL import Image
    except (ImportError, ModuleNotFoundError):
        Image = None
        missing.append("Pillow")
    if missing:
        raise EngineFailure(
            "blocked_by_runtime", "missing_python_packages",
            "绘图运行时不完整；未生成或伪造图件。", details={"missing": missing}, exit_code=EXIT_BLOCKED,
        )
    return pyplot, font_manager, Image


def configure_style(pyplot: Any, font_manager: Any, language: str, width: float, height: float) -> dict[str, Any]:
    available = {font.name for font in font_manager.fontManager.ttflist}
    chosen = "DejaVu Sans"
    if language == "zh":
        candidates = ["Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", "SimHei", "SimSun"]
        chosen = next((font for font in candidates if font in available), "")
        if not chosen:
            raise EngineFailure(
                "blocked_by_runtime", "missing_cjk_font", "未发现可用中文字体；无法保证中文图件没有方框。",
                details={"acceptable_fonts": candidates}, exit_code=EXIT_BLOCKED,
            )
    pyplot.rcParams.update({
        "font.family": chosen,
        "font.size": 8,
        "axes.labelsize": 8,
        "axes.titlesize": 9,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.unicode_minus": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "figure.figsize": (width, height),
        "figure.dpi": 100,
        "savefig.dpi": 300,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    return {"font": chosen, "palette": OKABE_ITO}


def numeric_column(table: dict[str, Any], name: str) -> list[float | None]:
    if name not in table["fields"]:
        raise EngineFailure("invalid_data", "column_missing", f"字段不存在：{name}")
    converted = [to_float(row[name]) for row in table["rows"]]
    bad = [index + 2 for index, (row, value) in enumerate(zip(table["rows"], converted)) if not is_missing(row[name]) and value is None]
    if bad:
        raise EngineFailure("invalid_data", "nonnumeric_values", f"字段 {name} 含非数值。", details={"rows": bad[:20]})
    return converted


def categories_in_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def select_chart(args: argparse.Namespace, profile: dict[str, Any]) -> str:
    if args.chart != "auto":
        return args.chart
    types = {column["name"]: column["type"] for column in profile["columns"]}
    if args.y and (args.group or (args.x and types.get(args.x) == "categorical")):
        return "box"
    if args.x and args.y and types.get(args.x) == "datetime":
        return "line"
    if args.x and args.y and types.get(args.x) == types.get(args.y) == "numeric":
        return "line" if args.ordered_x else "scatter"
    if args.y and types.get(args.y) == "numeric":
        return "hist"
    numeric = [column["name"] for column in profile["columns"] if column["type"] == "numeric"]
    if len(numeric) >= 3:
        return "heatmap"
    if numeric:
        args.y = numeric[0]
        return "hist"
    raise EngineFailure("invalid_data", "chart_ambiguous", "无法自动选图；请指定 --chart、--x 和 --y。")


def grouped_rows(table: dict[str, Any], group: str | None) -> dict[str, list[dict[str, str]]]:
    if not group:
        return {"all": table["rows"]}
    if group not in table["fields"]:
        raise EngineFailure("invalid_data", "group_column_missing", f"分组字段不存在：{group}")
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in table["rows"]:
        result[row[group] or "(missing)"].append(row)
    return dict(result)


def draw_chart(pyplot: Any, table: dict[str, Any], profile: dict[str, Any], args: argparse.Namespace, chart: str) -> tuple[Any, dict[str, Any]]:
    fig, ax = pyplot.subplots(figsize=(args.width, args.height), constrained_layout=True)
    groups = grouped_rows(table, args.group)
    metadata: dict[str, Any] = {"chart_type": chart, "group_sizes": {key: len(rows) for key, rows in groups.items()}, "warnings": []}

    if chart in {"line", "scatter"}:
        if not args.x or not args.y:
            raise EngineFailure("invalid_data", "axes_required", f"{chart} 需要 --x 与 --y。")
        numeric_column(table, args.y)
        x_type = next((column["type"] for column in profile["columns"] if column["name"] == args.x), None)
        if x_type not in {"numeric", "datetime"}:
            raise EngineFailure("invalid_data", "invalid_x_type", f"{chart} 的 x 必须是连续或时间字段。")
        for index, (label, rows) in enumerate(groups.items()):
            pairs: list[tuple[Any, float]] = []
            for row in rows:
                y_value = to_float(row[args.y])
                if y_value is None or is_missing(row[args.x]):
                    continue
                x_value: Any = to_float(row[args.x]) if x_type == "numeric" else datetime.fromisoformat(row[args.x].replace("Z", "+00:00"))
                if x_value is not None:
                    pairs.append((x_value, y_value))
            pairs.sort(key=lambda item: item[0])
            xs, ys = [item[0] for item in pairs], [item[1] for item in pairs]
            kwargs = {"color": OKABE_ITO[index % len(OKABE_ITO)], "label": label if args.group else None}
            if chart == "line":
                ax.plot(xs, ys, marker="o", linewidth=1.2, markersize=3.5, **kwargs)
            else:
                ax.scatter(xs, ys, s=24, edgecolors="white", linewidths=0.4, **kwargs)
        ax.set_xlabel(args.xlabel or args.x)
        ax.set_ylabel(args.ylabel or f"{args.y} ({args.units})")

    elif chart == "hist":
        target = args.y or args.x
        if not target:
            raise EngineFailure("invalid_data", "axis_required", "hist 需要 --y 或 --x。")
        values = [value for value in numeric_column(table, target) if value is not None]
        bins = args.bins or max(5, min(30, round(math.sqrt(len(values)))))
        ax.hist(values, bins=bins, color=OKABE_ITO[0], edgecolor="white", linewidth=0.6)
        ax.set_xlabel(args.xlabel or f"{target} ({args.units})")
        ax.set_ylabel(args.ylabel or "Count")

    elif chart == "box":
        target_group = args.group or args.x
        if not target_group or not args.y:
            raise EngineFailure("invalid_data", "axes_required", "box 需要 --y，并通过 --group 或 --x 指定类别。")
        if target_group not in table["fields"]:
            raise EngineFailure("invalid_data", "group_column_missing", f"类别字段不存在：{target_group}")
        labels = categories_in_order(row[target_group] for row in table["rows"] if row[target_group])
        data = [[to_float(row[args.y]) for row in table["rows"] if row[target_group] == label and to_float(row[args.y]) is not None] for label in labels]
        if not all(data):
            raise EngineFailure("invalid_data", "empty_group", "至少一个箱线分组没有有效数值。")
        boxes = ax.boxplot(data, labels=labels, patch_artist=True, showfliers=False)
        for patch, color in zip(boxes["boxes"], OKABE_ITO):
            patch.set_facecolor(color)
            patch.set_alpha(0.35)
        for group_index, values in enumerate(data, start=1):
            for point_index, value in enumerate(values):
                jitter = ((point_index % 7) - 3) * 0.018
                ax.scatter(group_index + jitter, value, s=18, color=OKABE_ITO[(group_index - 1) % len(OKABE_ITO)], edgecolors="white", linewidths=0.3, zorder=3)
        ax.set_xlabel(args.xlabel or target_group)
        ax.set_ylabel(args.ylabel or f"{args.y} ({args.units})")

    elif chart == "bar":
        if not args.x:
            raise EngineFailure("invalid_data", "axis_required", "bar 需要 --x 类别字段。")
        labels = categories_in_order(row[args.x] for row in table["rows"] if row[args.x])
        if args.y:
            data = [[to_float(row[args.y]) for row in table["rows"] if row[args.x] == label and to_float(row[args.y]) is not None] for label in labels]
            small = {label: len(values) for label, values in zip(labels, data) if len(values) < 10}
            if small and not args.allow_small_n_bar:
                raise EngineFailure(
                    "invalid_data", "small_n_bar_blocked",
                    "均值柱会掩盖小样本分布；请改用 box，或显式传 --allow-small-n-bar 并保留劝阻记录。",
                    details={"small_groups": small, "recommended_chart": "box"},
                )
            heights = [statistics.fmean(values) if values else math.nan for values in data]
            if small:
                metadata["warnings"].append("用户覆盖小样本均值柱拦截；应在图上叠加原始点并记录理由。")
        else:
            counts = Counter(row[args.x] for row in table["rows"] if row[args.x])
            heights = [counts[label] for label in labels]
        ax.bar(labels, heights, color=[OKABE_ITO[index % len(OKABE_ITO)] for index in range(len(labels))], edgecolor="black", linewidth=0.4)
        ax.set_xlabel(args.xlabel or args.x)
        ax.set_ylabel(args.ylabel or (f"Mean {args.y} ({args.units})" if args.y else "Count"))

    elif chart == "heatmap":
        selected = args.columns or [column["name"] for column in profile["columns"] if column["type"] == "numeric"]
        if len(selected) < 2:
            raise EngineFailure("invalid_data", "heatmap_columns", "heatmap 至少需要两个连续字段。")
        for column in selected:
            numeric_column(table, column)
        matrix: list[list[float]] = []
        for left in selected:
            row_values: list[float] = []
            for right in selected:
                pairs = [(to_float(row[left]), to_float(row[right])) for row in table["rows"]]
                valid = [(x, y) for x, y in pairs if x is not None and y is not None]
                value = pearson([x for x, _ in valid], [y for _, y in valid])
                row_values.append(value if value is not None else math.nan)
            matrix.append(row_values)
        image = ax.imshow(matrix, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(len(selected)), selected, rotation=45, ha="right")
        ax.set_yticks(range(len(selected)), selected)
        for row_index, row_values in enumerate(matrix):
            for col_index, value in enumerate(row_values):
                ax.text(col_index, row_index, "NA" if math.isnan(value) else f"{value:.2f}", ha="center", va="center", fontsize=6, color="black")
        colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
        colorbar.set_label("Pearson r")
        metadata["columns"] = selected
    else:
        raise EngineFailure("invalid_data", "unsupported_chart", f"不支持的图型：{chart}")

    if args.title:
        ax.set_title(args.title)
    if args.group and chart in {"line", "scatter"}:
        ax.legend(frameon=False, title=args.group)
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.5, alpha=0.7)
    return fig, metadata


def text_layout_checks(fig: Any) -> tuple[list[dict[str, Any]], list[str]]:
    checks: list[dict[str, Any]] = []
    warnings_found: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fig.canvas.draw()
    glyph = [str(item.message) for item in caught if "Glyph" in str(item.message) or "glyph" in str(item.message)]
    checks.append({"id": "glyphs", "status": "fail" if glyph else "pass", "details": glyph[:10]})
    renderer = fig.canvas.get_renderer()
    width, height = fig.canvas.get_width_height()
    clipped: list[str] = []
    for text in fig.findobj(match=lambda artist: hasattr(artist, "get_text") and hasattr(artist, "get_window_extent")):
        try:
            label = text.get_text().strip()
            if not label or not text.get_visible():
                continue
            box = text.get_window_extent(renderer)
            # Locators keep off-range tick artists that are wholly outside the
            # rendered canvas (for example ticks just beyond the chosen limits).
            # They are not drawn and therefore are not clipping failures.
            if box.x1 <= 0 or box.y1 <= 0 or box.x0 >= width or box.y0 >= height:
                continue
            # Matplotlib may place outer tick glyphs a few subpixels beyond the
            # canvas even with constrained layout.  Treat only material overflow
            # as clipping; visual QA still inspects the rendered PNG.
            tolerance = 12
            if box.x0 < -tolerance or box.y0 < -tolerance or box.x1 > width + tolerance or box.y1 > height + tolerance:
                clipped.append(label[:80])
        except Exception:
            continue
    checks.append({"id": "text_clipping", "status": "fail" if clipped else "pass", "details": clipped[:12]})
    for axis in fig.axes:
        for axis_name, labels in (("x", axis.get_xticklabels()), ("y", axis.get_yticklabels())):
            boxes = []
            for label in labels:
                if label.get_visible() and label.get_text().strip():
                    try:
                        boxes.append(label.get_window_extent(renderer))
                    except Exception:
                        pass
            overlap = any(boxes[i].overlaps(boxes[i + 1]) for i in range(len(boxes) - 1))
            if overlap:
                warnings_found.append(f"{axis_name} 轴相邻刻度标签可能重叠。")
    checks.append({"id": "tick_overlap", "status": "warn" if warnings_found else "pass", "details": warnings_found})
    return checks, warnings_found


def artifact_record(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def validate_artifacts(paths: dict[str, Path], Image: Any, width: float, height: float, dpi: int) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, path in paths.items():
        ok = path.is_file() and path.stat().st_size > 100
        checks.append({"id": f"artifact_{name}", "status": "pass" if ok else "fail", "details": str(path)})
    pdf = paths["pdf"].read_bytes()[:5] if paths["pdf"].is_file() else b""
    svg = paths["svg"].read_text(encoding="utf-8", errors="ignore")[:500] if paths["svg"].is_file() else ""
    checks.append({"id": "pdf_signature", "status": "pass" if pdf == b"%PDF-" else "fail", "details": pdf.decode("latin1", errors="ignore")})
    checks.append({"id": "svg_signature", "status": "pass" if "<svg" in svg else "fail", "details": "<svg present" if "<svg" in svg else "missing"})
    try:
        with Image.open(paths["png"]) as image:
            expected = (round(width * dpi), round(height * dpi))
            actual = image.size
            tolerance = max(3, round(max(expected) * 0.01))
            size_ok = abs(actual[0] - expected[0]) <= tolerance and abs(actual[1] - expected[1]) <= tolerance
            checks.append({"id": "png_dimensions", "status": "pass" if size_ok else "fail", "details": {"expected": expected, "actual": actual, "dpi": image.info.get("dpi")}})
        with Image.open(paths["grayscale_png"]) as gray:
            gray_ok = gray.mode in {"L", "LA"}
            checks.append({"id": "grayscale_mode", "status": "pass" if gray_ok else "fail", "details": gray.mode})
    except OSError as exc:
        checks.append({"id": "png_readable", "status": "fail", "details": str(exc)})
    return checks


def render_and_audit(fig: Any, pyplot: Any, Image: Any, output_dir: Path, figure_id: str, width: float, height: float, dpi: int) -> tuple[dict[str, Path], list[dict[str, Any]], list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "svg": output_dir / f"{figure_id}.svg",
        "pdf": output_dir / f"{figure_id}.pdf",
        "png": output_dir / f"{figure_id}.png",
        "grayscale_png": output_dir / f"{figure_id}.gray.png",
    }
    layout_checks, layout_warnings = text_layout_checks(fig)
    fig.set_size_inches(width, height, forward=True)
    fig.savefig(paths["svg"], format="svg", dpi=dpi, facecolor="white")
    fig.savefig(paths["pdf"], format="pdf", dpi=dpi, facecolor="white")
    fig.savefig(paths["png"], format="png", dpi=dpi, facecolor="white")
    with Image.open(paths["png"]) as image:
        image.convert("L").save(paths["grayscale_png"], dpi=(dpi, dpi))
    pyplot.close(fig)
    return paths, layout_checks + validate_artifacts(paths, Image, width, height, dpi), layout_warnings


def sample_size_note(table: dict[str, Any], args: argparse.Namespace) -> str:
    target = args.y or args.x
    if not target:
        return f"n={len(table['rows'])}"
    groups = grouped_rows(table, args.group or (args.x if args.chart in {"box", "bar"} else None))
    pieces = []
    for label, rows in groups.items():
        valid = sum(to_float(row[target]) is not None for row in rows) if target in table["fields"] else len(rows)
        pieces.append(f"{label}: n={valid}")
    return "; ".join(pieces)


def command_profile(args: argparse.Namespace) -> int:
    table = load_table(args.source, args.delimiter)
    profile = profile_table(table, args.group)
    if args.output:
        atomic_json_write(Path(args.output).expanduser().resolve(), profile)
        profile["written"] = str(Path(args.output).expanduser().resolve())
    emit(profile)
    return EXIT_OK


def command_preflight(_: argparse.Namespace) -> int:
    missing: list[str] = []
    versions: dict[str, str] = {}
    try:
        import matplotlib
        versions["matplotlib"] = getattr(matplotlib, "__version__", "unknown")
    except (ImportError, ModuleNotFoundError):
        missing.append("matplotlib")
    try:
        import PIL
        versions["Pillow"] = getattr(PIL, "__version__", "unknown")
    except (ImportError, ModuleNotFoundError):
        missing.append("Pillow")
    payload = {"status": "ready" if not missing else "blocked_by_runtime", "missing": missing, "versions": versions}
    emit(payload)
    return EXIT_OK if not missing else EXIT_BLOCKED


def command_plot(args: argparse.Namespace) -> int:
    table = load_table(args.source, args.delimiter)
    profile = profile_table(table, [args.group] if args.group else [])
    pyplot, font_manager, Image = require_plot_runtime()
    style = configure_style(pyplot, font_manager, args.language, args.width, args.height)
    chart = select_chart(args, profile)
    args.chart = chart
    fig, plot_metadata = draw_chart(pyplot, table, profile, args, chart)
    output_dir = Path(args.output_dir).expanduser().resolve()
    paths, checks, layout_warnings = render_and_audit(fig, pyplot, Image, output_dir, args.figure_id, args.width, args.height, args.dpi)
    hard_failures = [check for check in checks if check["status"] == "fail"]
    caption_metadata = {
        "caption": args.caption.strip(),
        "sample_size": args.sample_size_note.strip() if args.sample_size_note else sample_size_note(table, args),
        "units": args.units.strip(),
        "error_definition": args.error_definition.strip(),
        "statistical_test": args.statistical_test.strip(),
        "multiple_comparison": args.multiple_comparison.strip(),
    }
    caption_path = output_dir / f"{args.figure_id}.caption.md"
    caption_path.parent.mkdir(parents=True, exist_ok=True)
    caption_path.write_text(
        "\n".join([
            f"# {args.figure_id} 图注与统计披露", "", args.caption.strip(), "",
            f"- Claim ID: {args.claim_id}",
            f"- Sample size: {caption_metadata['sample_size']}",
            f"- Units: {caption_metadata['units']}",
            f"- Error definition: {caption_metadata['error_definition']}",
            f"- Statistical test: {caption_metadata['statistical_test']}",
            f"- Multiple comparison: {caption_metadata['multiple_comparison']}",
        ]) + "\n",
        encoding="utf-8",
    )
    paths["caption"] = caption_path
    qa_path = output_dir / f"{args.figure_id}.qa.json"
    qa = {
        "schema_version": 1,
        "status": "qa_failed" if hard_failures else "passed",
        "figure_id": args.figure_id,
        "claim_id": args.claim_id,
        "claim": args.claim,
        "target_journal": args.target_journal,
        "chart_type": chart,
        "source": {"path": table["label"], "sha256": table["sha256"], "rows": len(table["rows"])},
        "style": {**style, "language": args.language, "width_inches": args.width, "height_inches": args.height, "dpi": args.dpi},
        "artifacts": {name: artifact_record(path) for name, path in paths.items()},
        "caption_metadata": caption_metadata,
        "checks": checks,
        "warnings": plot_metadata.get("warnings", []) + layout_warnings,
        "profile_summary": {"columns": profile["columns"], "group_counts": profile["group_counts"], "suggestions": profile["suggestions"]},
    }
    atomic_json_write(qa_path, qa)
    payload = {
        "status": qa["status"],
        "qa_path": str(qa_path),
        "artifacts": qa["artifacts"],
        "failures": hard_failures,
        "register_row": {
            "figure_id": args.figure_id, "figure_type": "data", "claim_id": args.claim_id, "claim": args.claim,
            "source_data_path": table["label"], "target_journal": args.target_journal,
            "vector_path": str(paths["svg"]), "output_path": str(paths["svg"]), "preview_path": str(paths["png"]),
            "grayscale_path": str(paths["grayscale_png"]), "qa_report_path": str(qa_path),
            "caption_path": str(caption_path), "statistics_disclosure": json.dumps(caption_metadata, ensure_ascii=False, separators=(",", ":")),
            "data_hash": table["sha256"],
            "qa_status": qa["status"], "prompt_id": "", "placeholder_token": "",
            "notes": f"QA: {qa_path.name}; chart={chart}",
        },
        "manuscript_reference": f"<!-- DATA_FIGURE: {args.figure_id} -->",
    }
    emit(payload)
    return EXIT_INVALID if hard_failures else EXIT_OK


def add_common_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", required=True, help="UTF-8 CSV/TSV；使用 - 从 stdin 读取。")
    parser.add_argument("--delimiter", choices=["auto", "comma", "tab"], default="auto")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立科研数据图引擎；不调用任何全局 SKILL。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="检查绘图运行时。")
    preflight.set_defaults(func=command_preflight)

    profile = subparsers.add_parser("profile", help="确定性剖析 CSV/TSV 并给出图型建议。")
    add_common_source(profile)
    profile.add_argument("--group", action="append", default=[], help="可重复传入分组字段。")
    profile.add_argument("--output", help="可选 JSON 报告路径。")
    profile.set_defaults(func=command_profile)

    plot = subparsers.add_parser("plot", help="绘图、导出并生成 QA JSON。")
    add_common_source(plot)
    plot.add_argument("--figure-id", required=True)
    plot.add_argument("--claim-id", required=True, help="该图支撑的稳定 Claim ID。")
    plot.add_argument("--claim", required=True, help="该图唯一支撑的核心主张。")
    plot.add_argument("--target-journal", required=True)
    plot.add_argument("--chart", choices=["auto", "line", "scatter", "bar", "box", "hist", "heatmap"], default="auto")
    plot.add_argument("--x")
    plot.add_argument("--y")
    plot.add_argument("--group")
    plot.add_argument("--columns", nargs="+", help="heatmap 使用的连续字段。")
    plot.add_argument("--ordered-x", action="store_true", help="把连续 x 解释为有序过程，auto 时选择 line。")
    plot.add_argument("--allow-small-n-bar", action="store_true")
    plot.add_argument("--bins", type=int)
    plot.add_argument("--output-dir", required=True)
    plot.add_argument("--language", choices=["en", "zh"], default="en")
    plot.add_argument("--width", type=float, default=7.2)
    plot.add_argument("--height", type=float, default=4.8)
    plot.add_argument("--dpi", type=int, default=300)
    plot.add_argument("--title")
    plot.add_argument("--xlabel")
    plot.add_argument("--ylabel")
    plot.add_argument("--caption", required=True)
    plot.add_argument("--sample-size-note")
    plot.add_argument("--units", required=True, help="明确单位；无量纲时写 dimensionless。")
    plot.add_argument("--error-definition", default="not_applicable", help="SD/SEM/95%% CI/IQR 或 not_applicable。")
    plot.add_argument("--statistical-test", default="not_applicable")
    plot.add_argument("--multiple-comparison", default="not_applicable")
    plot.set_defaults(func=command_plot)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except EngineFailure as exc:
        emit({"status": exc.status, "code": exc.code, "message": exc.message, "details": exc.details})
        return exc.exit_code
    except (OSError, csv.Error, ValueError, json.JSONDecodeError) as exc:
        emit({"status": "error", "code": "unhandled_io_or_data_error", "message": str(exc)})
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
