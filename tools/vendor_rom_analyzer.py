#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rom-analyzer 저장소의 런타임 패키지를 Game Books 배포용 libs/에 동기화한다.

원본은 rom-analyzer 저장소가 유일한 진실의 원천이며,
Game Books의 libs/rom_analyzer와 libs/rom_database는 동일 커밋의
배포 시점 스냅샷으로만 취급한다.
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
PACKAGE_NAMES = ("rom_analyzer", "rom_database")


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
    source_packages = {name: source_root / name for name in PACKAGE_NAMES}
    for name, package_root in source_packages.items():
        if not package_root.is_dir() or not (package_root / "__init__.py").is_file():
            raise SystemExit(f"{name} 패키지를 찾을 수 없습니다: {package_root}")

    libs_root = PLUGIN_ROOT / "libs"
    libs_root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "source": "rom-analyzer",
        "version": _package_version(source_packages["rom_analyzer"]),
        "git_commit": _git_commit(source_root),
        "packages": list(PACKAGE_NAMES),
    }

    with tempfile.TemporaryDirectory(prefix="rom_analyzer_vendor_", dir=str(libs_root)) as temp_dir:
        stage_root = Path(temp_dir)
        staged_packages = {}
        for name, package_root in source_packages.items():
            staged = stage_root / name
            shutil.copytree(package_root, staged, ignore=_ignore)
            (staged / "VENDORED_FROM.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            staged_packages[name] = staged

        backups = {}
        installed = []
        try:
            for name in PACKAGE_NAMES:
                destination = libs_root / name
                if destination.exists():
                    backup = libs_root / f".{name}.old"
                    if backup.exists():
                        shutil.rmtree(backup)
                    os.replace(destination, backup)
                    backups[name] = backup

            for name in PACKAGE_NAMES:
                destination = libs_root / name
                os.replace(staged_packages[name], destination)
                installed.append(name)
        except Exception:
            for name in reversed(installed):
                destination = libs_root / name
                if destination.exists():
                    shutil.rmtree(destination)
            for name, backup in backups.items():
                destination = libs_root / name
                if backup.exists() and not destination.exists():
                    os.replace(backup, destination)
            raise
        finally:
            for backup in backups.values():
                if backup.exists():
                    shutil.rmtree(backup)

    print(f"rom-analyzer vendor 완료: {libs_root / 'rom_analyzer'}")
    print(f"rom-database vendor 완료: {libs_root / 'rom_database'}")
    print(f"버전: {metadata['version']}")
    print(f"커밋: {metadata['git_commit'] or 'unknown'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="rom-analyzer와 rom-database를 Game Books libs/에 동기화")
    parser.add_argument(
        "--source",
        default=os.environ.get("ROM_ANALYZER_SOURCE", str(DEFAULT_SOURCE)),
        help="rom-analyzer 저장소 경로",
    )
    args = parser.parse_args()
    vendor(args.source)
