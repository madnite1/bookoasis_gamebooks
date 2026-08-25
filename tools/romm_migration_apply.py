#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/romm_migration_apply.py

BookOasis 기존 라이브러리(roms/, bios/, covers/) 데이터를 RomM 표준 구조로 복사(Copy-first) 마이그레이션하는 실행 엔진.
외부 rclone.conf 파일에 전혀 의존하지 않고, 사용자가 입력한 remote config 문자열로부터 자체 임시 config를 생성하여 사용합니다.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.romm_migration_config import (
    BIOS_MAP_PATH,
    COVER_MAP_PATH,
    MANIFEST_PATH,
    TARGET_LIBRARY_DIR,
)

logger = logging.getLogger("bookoasis_gamebooks.romm_migration_apply")

_TEMP_CONFIG_PATH = "/tmp/gamebooks_rclone.conf"


# --------------------------------------------------------------------------
# rclone config 생성 및 서버 사이드 복사 헬퍼
# --------------------------------------------------------------------------
def is_google_drive_mount(path: str | Path) -> bool:
    """주어진 경로가 구글 드라이브(또는 클라우드 rclone 마운트) 폴더인지 확인"""
    p = Path(path)
    if not p.exists():
        return False
    path_str = str(p.resolve())
    # 일반적인 클라우드 마운트 접두사 확인
    if path_str.startswith("/mnt/gdrive") or "/gdrive" in path_str or "/google_drive" in path_str:
        return True
    try:
        # mountpoint 판별
        res = subprocess.run(["df", "-T", path_str], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            out = res.stdout.lower()
            if "rclone" in out or "fuse" in out or "google" in out:
                return True
    except Exception:
        pass
    return False


def setup_rclone_custom_config(config_text: str) -> Optional[str]:
    """사용자가 입력한 rclone.conf 섹션 본문으로 /tmp/gamebooks_rclone.conf 파일 생성"""
    if not config_text or not config_text.strip():
        return None
    try:
        with open(_TEMP_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(config_text.strip() + "\n")
        os.chmod(_TEMP_CONFIG_PATH, 0o600)
        return _TEMP_CONFIG_PATH
    except Exception as e:
        logger.error(f"Failed to write custom rclone config to {_TEMP_CONFIG_PATH}: {e}")
        return None


def cleanup_rclone_custom_config():
    """임시 rclone config 파일 정리"""
    try:
        if os.path.exists(_TEMP_CONFIG_PATH):
            os.remove(_TEMP_CONFIG_PATH)
    except Exception:
        pass


def _safe_copy_file(
    src: str | Path,
    dst: str | Path,
    overwrite: bool = False,
    rclone_config_path: Optional[str] = None,
    rclone_remote: Optional[str] = None,
    rclone_mount_base: Optional[str] = None,
) -> bool:
    src_p = Path(src)
    dst_p = Path(dst)
    if not src_p.is_file():
        return False

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    if dst_p.exists() and not overwrite:
        if dst_p.stat().st_size == src_p.stat().st_size:
            return True

    # 사용자 config가 있고 마운트 경로 내부일 경우 rclone server-side copy 시도
    if rclone_config_path and os.path.isfile(rclone_config_path) and rclone_remote and rclone_mount_base:
        try:
            src_str = str(src_p.resolve())
            dst_str = str(dst_p.resolve())
            base_str = str(Path(rclone_mount_base).resolve())
            if src_str.startswith(base_str) and dst_str.startswith(base_str):
                rel_src = src_str[len(base_str):].lstrip("/\\")
                rel_dst = dst_str[len(base_str):].lstrip("/\\")
                cmd = [
                    "rclone", "copyto",
                    f"{rclone_remote}:{rel_src}",
                    f"{rclone_remote}:{rel_dst}",
                    "--config", rclone_config_path,
                    "--drive-server-side-across-configs",
                    "--fast-list",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if res.returncode == 0:
                    return True
                logger.debug(f"rclone copyto fallback ({rel_src} -> {rel_dst}): {res.stderr.strip()}")
        except Exception as ex:
            logger.debug(f"rclone copyto error ({src_p} -> {dst_p}): {ex}")

    # 일반 로컬 복사 폴백
    shutil.copy2(src_p, dst_p)
    return True


def execute_migration_plan(
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    manifest_path: Optional[Path] = None,
    bios_map_path: Optional[Path] = None,
    cover_map_path: Optional[Path] = None,
    rclone_config_content: Optional[str] = None,
    rclone_remote: Optional[str] = None,
    rclone_mount_base: Optional[str] = None,
) -> Dict[str, Any]:
    manifest_f = manifest_path or MANIFEST_PATH
    bios_map_f = bios_map_path or BIOS_MAP_PATH
    cover_map_f = cover_map_path or COVER_MAP_PATH

    # 사용자 config 내용이 전달되면 자체 임시 config 파일 준비
    rclone_config_path = None
    if rclone_config_content and rclone_config_content.strip():
        rclone_config_path = setup_rclone_custom_config(rclone_config_content)
    elif os.path.isfile(_TEMP_CONFIG_PATH):
        rclone_config_path = _TEMP_CONFIG_PATH

    if not manifest_f.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_f}")

    with open(manifest_f, "r", encoding="utf-8") as f:
        manifest: List[Dict[str, Any]] = json.load(f)

    bios_map: List[Dict[str, Any]] = []
    if bios_map_f.is_file():
        with open(bios_map_f, "r", encoding="utf-8") as f:
            bios_map = json.load(f)

    cover_map: List[Dict[str, Any]] = []
    if cover_map_f.is_file():
        with open(cover_map_f, "r", encoding="utf-8") as f:
            cover_map = json.load(f)

    total_steps = len(manifest) + len(bios_map) + len(cover_map)
    completed_steps = 0
    copied_rom_files = 0
    copied_bios_files = 0
    copied_cover_files = 0
    failed_items: List[Dict[str, Any]] = []

    def emit(phase: str, current_item: str, details: str = ""):
        if progress_cb:
            pct = int((completed_steps / total_steps) * 100) if total_steps > 0 else 100
            progress_cb({
                "phase": phase,
                "current": completed_steps,
                "total": total_steps,
                "percent": min(100, max(0, pct)),
                "current_item": current_item,
                "details": details,
                "copied_rom_files": copied_rom_files,
                "copied_bios_files": copied_bios_files,
                "copied_cover_files": copied_cover_files,
                "failed_count": len(failed_items),
                "is_rclone_mode": bool(rclone_config_path and rclone_remote),
                "rclone_remote": rclone_remote or "",
                "rclone_mount_base": rclone_mount_base or "",
            })

    emit("start", "마이그레이션 시작 준비", f"총 {total_steps}건 계획 로드 완료")

    # 1. ROM 파일 및 번들 복사
    for idx, item in enumerate(manifest, 1):
        src_path = item.get("source_path") or ""
        target_paths = item.get("target_paths") or []
        bundle_files = item.get("bundle_files") or [src_path]
        title = item.get("title_guess") or os.path.basename(src_path)

        completed_steps += 1
        if idx % 10 == 0 or idx == 1 or idx == len(manifest):
            emit("rom", title, f"ROM 복사 중 ({idx}/{len(manifest)}): {os.path.basename(src_path)}")

        try:
            target_rom_dir = Path(item.get("target_rom_dir") or (TARGET_LIBRARY_DIR / item.get("target_platform_slug", "unknown") / "roms"))
            target_rom_dir.mkdir(parents=True, exist_ok=True)

            if len(bundle_files) == 1 and target_paths:
                dst = target_paths[0]
                if _safe_copy_file(src_path, dst, rclone_config_path=rclone_config_path, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base):
                    copied_rom_files += 1
                else:
                    failed_items.append({"type": "rom", "src": src_path, "dst": dst, "error": "file not found"})
            else:
                for b_file in bundle_files:
                    b_dst = target_rom_dir / os.path.basename(b_file)
                    if _safe_copy_file(b_file, b_dst, rclone_config_path=rclone_config_path, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base):
                        copied_rom_files += 1
                    else:
                        failed_items.append({"type": "rom_bundle", "src": b_file, "dst": str(b_dst), "error": "bundle file missing"})

            if item.get("rewrite_descriptor") and target_paths:
                cue_dst = target_paths[0]
                try:
                    from bookoasis_gamebooks import _rewrite_disk_manifest_to_local_paths
                    _rewrite_disk_manifest_to_local_paths(cue_dst)
                except Exception as ex:
                    logger.warning(f"Descriptor rewrite failed on {cue_dst}: {ex}")

        except Exception as e:
            failed_items.append({"type": "rom_exception", "src": src_path, "error": str(e)})

        # 50개마다 짧은 딜레이로 시스템 리소스 보호
        if idx % 50 == 0:
            time.sleep(0.05)

    # 2. BIOS 파일 복사
    for idx, item in enumerate(bios_map, 1):
        src_path = item.get("source_bios") or item.get("source_path") or ""
        target_path = item.get("target_path") or ""
        completed_steps += 1
        b_name = item.get("source_name") or os.path.basename(src_path)
        if idx % 10 == 0 or idx == 1 or idx == len(bios_map):
            emit("bios", b_name, f"BIOS 복사 중 ({idx}/{len(bios_map)}): {b_name}")

        try:
            if target_path and _safe_copy_file(src_path, target_path, rclone_config_path=rclone_config_path, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base):
                copied_bios_files += 1
            else:
                failed_items.append({"type": "bios", "src": src_path, "dst": target_path, "error": "copy failed"})
        except Exception as e:
            failed_items.append({"type": "bios_exception", "src": src_path, "error": str(e)})

    # 3. Cover 아트 복사
    for idx, item in enumerate(cover_map, 1):
        src_cover = item.get("source_cover") or ""
        target_resource = item.get("target_resource_dir") or ""
        target_cover_path = item.get("target_cover_path") or ""
        target_asset = item.get("target_asset_path") or ""
        c_name = item.get("cover_name") or os.path.basename(src_cover)
        completed_steps += 1
        if idx % 10 == 0 or idx == 1 or idx == len(cover_map):
            emit("cover", c_name, f"커버 아트 복사 중 ({idx}/{len(cover_map)}): {c_name}")

        try:
            copied = False
            if target_cover_path:
                if _safe_copy_file(src_cover, target_cover_path, rclone_config_path=rclone_config_path, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base):
                    copied = True
            elif target_resource:
                c_ext = os.path.splitext(src_cover)[1].lower() or ".png"
                dst_cover = Path(target_resource) / f"cover{c_ext}"
                if _safe_copy_file(src_cover, dst_cover, rclone_config_path=rclone_config_path, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base):
                    copied = True
            if target_asset:
                if _safe_copy_file(src_cover, target_asset, rclone_config_path=rclone_config_path, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base):
                    copied = True
            if copied:
                copied_cover_files += 1
            else:
                failed_items.append({"type": "cover", "src": src_cover, "error": "target unspecified or missing"})
        except Exception as e:
            failed_items.append({"type": "cover_exception", "src": src_cover, "error": str(e)})

        # 50개마다 짧은 딜레이로 시스템 리소스 보호
        if idx % 50 == 0:
            time.sleep(0.05)

    emit("completed", "마이그레이션 완료", f"ROM: {copied_rom_files}, BIOS: {copied_bios_files}, Cover: {copied_cover_files}")

    return {
        "success": len(failed_items) == 0,
        "total_steps": total_steps,
        "copied_rom_files": copied_rom_files,
        "copied_bios_files": copied_bios_files,
        "copied_cover_files": copied_cover_files,
        "failed_count": len(failed_items),
        "failed_items": failed_items[:50],
        "is_rclone_mode": bool(rclone_config_path and rclone_remote),
        "rclone_remote": rclone_remote or "",
        "rclone_mount_base": rclone_mount_base or "",
    }


if __name__ == "__main__":
    def cli_progress(p: Dict[str, Any]):
        print(f"[{p['percent']}%] ({p['phase']}) {p['current_item']} - {p['details']}", flush=True)

    rclone_cfg = os.environ.get("RCLONE_CONFIG_CONTENT")
    rclone_remote = os.environ.get("RCLONE_REMOTE", "google_drive")
    rclone_mount = os.environ.get("RCLONE_MOUNT_BASE", "/mnt/gdrive")

    res = execute_migration_plan(
        progress_cb=cli_progress,
        rclone_config_content=rclone_cfg,
        rclone_remote=rclone_remote,
        rclone_mount_base=rclone_mount,
    )
    print("\nResult:", json.dumps(res, ensure_ascii=False, indent=2))
