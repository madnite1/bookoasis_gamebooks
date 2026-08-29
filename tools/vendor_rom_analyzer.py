#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rom-analyzer 원본 패키지를 Game Books 배포용 libs/에 동기화한다.

원본은 rom-analyzer 저장소가 유일한 진실의 원천이며,
Game Books의 libs/rom_analyzer는 배포 시점 스냅샷으로만 취급한다.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (PLUGIN_ROOT / ".." / ".." / "rom-analyzer").resolve()
DESTINATION = PLUGIN_ROOT / "libs" / "rom_analyzer"


def _ignore(_directory, names):
    ignored = set()
    for name in names:
        if name in {"__pycache__", "tests"} or name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _git_commit(source_root):
    try:
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={source_root}", "-C", str(source_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _package_version(package_root):
    init_text = (package_root / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def vendor(source_root):
    source_root = Path(source_root).expanduser().resolve()
    package_root = source_root / "rom_analyzer"
    if not package_root.is_dir() or not (package_root / "__init__.py").is_file():
        raise SystemExit(f"rom_analyzer 패키지를 찾을 수 없습니다: {package_root}")

    libs_root = DESTINATION.parent
    libs_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="rom_analyzer_vendor_", dir=str(libs_root)) as temp_dir:
        staged = Path(temp_dir) / "rom_analyzer"
        shutil.copytree(package_root, staged, ignore=_ignore)

        metadata = {
            "source": "rom-analyzer",
            "version": _package_version(package_root),
            "git_commit": _git_commit(source_root),
        }
        (staged / "VENDORED_FROM.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        old_destination = None
        if DESTINATION.exists():
            old_destination = libs_root / ".rom_analyzer.old"
            if old_destination.exists():
                shutil.rmtree(old_destination)
            os.replace(DESTINATION, old_destination)
        try:
            os.replace(staged, DESTINATION)
        except Exception:
            if old_destination and old_destination.exists() and not DESTINATION.exists():
                os.replace(old_destination, DESTINATION)
            raise
        if old_destination and old_destination.exists():
            shutil.rmtree(old_destination)

    print(f"rom-analyzer vendor 완료: {DESTINATION}")
    print(f"버전: {metadata['version']}")
    print(f"커밋: {metadata['git_commit'] or 'unknown'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="rom-analyzer를 Game Books libs/에 동기화")
    parser.add_argument(
        "--source",
        default=os.environ.get("ROM_ANALYZER_SOURCE", str(DEFAULT_SOURCE)),
        help="rom-analyzer 저장소 경로",
    )
    args = parser.parse_args()
    vendor(args.source)
