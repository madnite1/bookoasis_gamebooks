#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""기존 Game Books 판정과 vendored rom-analyzer를 읽기 전용으로 비교한다.

기본 모드는 기존 감지기를 다시 실행한다. --baseline-db를 주면 games 테이블에 이미 저장된
판정값을 legacy 기준선으로 사용해 원격 ROM을 한 번만 읽는다. DB와 ROM은 수정하지 않는다.
"""

import argparse
import concurrent.futures
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBS = ROOT / "libs"
for path in (str(ROOT), str(LIBS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from bookoasis_gamebooks import _detect_rom_info  # noqa: E402
from rom_analysis_adapter import analyze_rom, get_vendor_info  # noqa: E402

CORE_ALIASES = {
    "segaMD": "megadrive",
    "segaMS": "mastersystem",
    "mame2003": "arcade",
}
PLATFORM_ALIASES = {
    "Genesis": "genesis",
    "Mega Drive": "genesis",
    "Neo-Geo": "neogeo",
    "Arcade": "arcade",
    "PS1": "ps1",
    "PlayStation": "ps1",
}


def _norm_text(value):
    return str(value or "").strip().casefold()


def _norm_core(value):
    value = str(value or "").strip()
    return CORE_ALIASES.get(value, value).casefold()


def _norm_platform(value):
    value = str(value or "").strip()
    return PLATFORM_ALIASES.get(value, value).casefold()


def _norm_list(value):
    if not value:
        return []
    if isinstance(value, str):
        value = [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
    return sorted({_norm_text(v) for v in value if _norm_text(v)})


def _db_baselines(db_path):
    con = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT file_path, title, game_code, maker_code, core, platform, needed_bios, "
        "serial_code, source_system, metadata_source, metadata_confidence, missing_roms "
        "FROM games WHERE file_path IS NOT NULL AND trim(file_path) <> ''"
    ).fetchall()
    con.close()
    result = {}
    for row in rows:
        path = os.path.abspath(os.path.expanduser(row["file_path"]))
        result[path] = {
            "title": row["title"] or "",
            "game_code": row["game_code"] or "",
            "maker_code": row["maker_code"] or "",
            "core": row["core"] or "",
            "platform": row["platform"] or "",
            "needed_bios": row["needed_bios"] or "",
            "serial_code": row["serial_code"] or "",
            "source_system": row["source_system"] or "",
            "metadata_source": row["metadata_source"] or "",
            "metadata_confidence": int(row["metadata_confidence"] or 0),
            "disk_missing_files": [v for v in str(row["missing_roms"] or "").split(",") if v],
            "resolved_disk_files": [],
            "disc_count": 1,
        }
    return result


def _compare(item):
    path, baseline = item
    started = time.perf_counter()
    row = {"path": path, "issues": [], "severity": "same"}

    if baseline is None:
        try:
            legacy = _detect_rom_info(path)
            row["legacy"] = legacy
        except Exception as exc:
            row["legacy_error"] = f"{type(exc).__name__}: {exc}"
            row["issues"].append("legacy_error")
            legacy = None
    else:
        legacy = baseline
        row["legacy"] = legacy

    try:
        modern = analyze_rom(path)
        row["modern"] = modern
    except Exception as exc:
        row["modern_error"] = f"{type(exc).__name__}: {exc}"
        row["issues"].append("modern_error")
        modern = None

    if legacy is not None and modern is not None:
        legacy_skip = legacy.get("platform") == "_skip_"
        modern_skip = modern.get("platform") == "_skip_"
        if legacy_skip != modern_skip:
            row["issues"].append("skip_mismatch")
        if _norm_core(legacy.get("core")) != _norm_core(modern.get("core")):
            row["issues"].append("core_mismatch")
        if _norm_platform(legacy.get("platform")) != _norm_platform(modern.get("platform")):
            row["issues"].append("platform_mismatch")
        if baseline is None and legacy.get("title") and modern.get("title") and _norm_text(legacy.get("title")) != _norm_text(modern.get("title")):
            row["issues"].append("title_mismatch")
        if legacy.get("serial_code") and modern.get("serial_code") and _norm_text(legacy.get("serial_code")) != _norm_text(modern.get("serial_code")):
            row["issues"].append("serial_mismatch")
        if baseline is None and _norm_list(legacy.get("disk_missing_files")) != _norm_list(modern.get("disk_missing_files")):
            row["issues"].append("disk_missing_mismatch")
        legacy_bios = _norm_list(legacy.get("needed_bios"))
        modern_bios = _norm_list(modern.get("needed_bios"))
        if legacy_bios and modern_bios and legacy_bios != modern_bios:
            row["issues"].append("bios_mismatch")

    critical = {"modern_error", "skip_mismatch", "core_mismatch", "platform_mismatch"}
    row["severity"] = "critical" if critical.intersection(row["issues"]) else ("metadata" if row["issues"] else "same")
    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return row


def _load_items(args):
    baselines = _db_baselines(args.baseline_db) if args.baseline_db else {}
    values = list(args.paths or [])
    if args.path_file:
        values.extend(line.strip() for line in Path(args.path_file).read_text(encoding="utf-8").splitlines())
    if args.baseline_db and not values:
        values.extend(baselines.keys())

    items = []
    seen = set()
    missing = 0
    for value in values:
        path = os.path.abspath(os.path.expanduser(value))
        if path in seen:
            continue
        seen.add(path)
        if not os.path.isfile(path):
            missing += 1
            continue
        items.append((path, baselines.get(path) if args.baseline_db else None))
    return items, missing, len(values)


def main():
    parser = argparse.ArgumentParser(description="Game Books legacy 판정과 rom-analyzer shadow 비교")
    parser.add_argument("paths", nargs="*", help="비교할 ROM 파일")
    parser.add_argument("--path-file", help="ROM 절대경로가 한 줄에 하나씩 있는 파일")
    parser.add_argument("--baseline-db", help="현재 games 판정값을 기준선으로 사용할 Game Books SQLite DB")
    parser.add_argument("--output", help="상세 JSON 보고서 저장 경로")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    items, missing_files, requested = _load_items(args)
    if args.limit > 0:
        items = items[: args.limit]
    if not items:
        raise SystemExit("비교할 ROM 파일이 없습니다.")

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        rows = list(executor.map(_compare, items))

    issue_counts = Counter(issue for row in rows for issue in row["issues"])
    severity_counts = Counter(row["severity"] for row in rows)
    platform_pairs = Counter(
        (str((row.get("legacy") or {}).get("platform") or ""), str((row.get("modern") or {}).get("platform") or ""))
        for row in rows if "platform_mismatch" in row["issues"]
    )
    core_pairs = Counter(
        (str((row.get("legacy") or {}).get("core") or ""), str((row.get("modern") or {}).get("core") or ""))
        for row in rows if "core_mismatch" in row["issues"]
    )
    report = {
        "vendor": get_vendor_info(),
        "summary": {
            "requested": requested,
            "missing_files": missing_files,
            "total": len(rows),
            "same": severity_counts["same"],
            "metadata_diff": severity_counts["metadata"],
            "critical_diff": severity_counts["critical"],
            "issue_counts": dict(issue_counts),
            "platform_mismatch_pairs": [
                {"legacy": legacy, "modern": modern, "count": count}
                for (legacy, modern), count in platform_pairs.most_common(30)
            ],
            "core_mismatch_pairs": [
                {"legacy": legacy, "modern": modern, "count": count}
                for (legacy, modern), count in core_pairs.most_common(30)
            ],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
        "critical_examples": [row for row in rows if row["severity"] == "critical"][:100],
        "metadata_examples": [row for row in rows if row["severity"] == "metadata"][:50],
        "rows": rows,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
