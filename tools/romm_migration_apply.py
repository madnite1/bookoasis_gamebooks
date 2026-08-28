#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""romm_tools/romm_migration_apply.py

BookOasis 기존 라이브러리(roms/, bios/, covers/) 데이터를 RomM 표준 구조로 복사(Copy-first) 마이그레이션하는 실행 엔진.
코어 utils.rclone_gdrive_copy의 서버사이드 일괄 묶음 복사(Batch copy) 기능을 사용하여 고속으로 마이그레이션을 수행합니다.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
# Resolve both deployed layout (<project>/plugins/metadata/bookoasis_gamebooks)
# and development layout (<workspace>/BookOasis_plugins/bookoasis_gamebooks).
_PROJECT_CANDIDATES = []
if ROOT_DIR.parent.name == "metadata" and ROOT_DIR.parent.parent.name == "plugins":
    _PROJECT_CANDIDATES.append(ROOT_DIR.parent.parent.parent)
_PROJECT_CANDIDATES.extend([
    ROOT_DIR.parent.parent / "BookOasis",
    Path("/app"),
])
PROJECT_ROOT = next(
    (p for p in _PROJECT_CANDIDATES if (p / "plugins" / "metadata" / "base.py").is_file()),
    _PROJECT_CANDIDATES[0],
)
for _import_root in (PROJECT_ROOT, ROOT_DIR):
    _root_s = str(_import_root)
    if _root_s not in sys.path:
        sys.path.insert(0, _root_s)

from .romm_migration_config import (
    BIOS_MAP_PATH,
    CANCEL_FLAG_PATH,
    COVER_MAP_PATH,
    MANIFEST_PATH,
    SOURCE_ROOT,
    TARGET_LIBRARY_DIR,
    TARGET_ROMM_ROOT,
)

logger = logging.getLogger("bookoasis_gamebooks.romm_migration_apply")


def _path_within(path: str | Path, root: str | Path) -> bool:
    try:
        p = Path(path).expanduser().resolve(strict=False)
        r = Path(root).expanduser().resolve(strict=False)
        return p == r or r in p.parents
    except Exception:
        return False


def _is_expected_romm_root(path: str | Path) -> bool:
    try:
        p = Path(path).expanduser().resolve(strict=False)
        expected = TARGET_ROMM_ROOT.expanduser().resolve(strict=False)
        return p == expected and p.name == "romm_library" and _path_within(p, SOURCE_ROOT)
    except Exception:
        return False


# --------------------------------------------------------------------------
# 서버 사이드 복사 및 유틸리티 헬퍼
# --------------------------------------------------------------------------
def is_google_drive_mount(path: str | Path) -> bool:
    """주어진 경로가 구글 드라이브(또는 클라우드 rclone 마운트) 폴더인지 확인"""
    p = Path(path)
    if not p.exists():
        return False
    path_str = str(p.resolve())
    if path_str.startswith("/mnt/gdrive") or "/gdrive" in path_str or "/google_drive" in path_str:
        return True
    try:
        if os.path.exists("/proc/mounts"):
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        fstype = parts[2].lower()
                        mpoint = parts[1]
                        if path_str == mpoint or path_str.startswith(mpoint + os.sep):
                            if "rclone" in fstype or "fuse" in fstype:
                                return True
    except Exception:
        pass
    return False


def is_migration_cancelled() -> bool:
    """마이그레이션 취소 플래그 파일 존재 여부 확인"""
    try:
        return CANCEL_FLAG_PATH.is_file()
    except Exception:
        return False


def request_cancel_migration() -> bool:
    """마이그레이션 취소 플래그 파일 생성"""
    try:
        CANCEL_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CANCEL_FLAG_PATH.touch()
        return True
    except Exception as e:
        logger.error(f"Failed to set cancel flag: {e}")
        return False


def clear_cancel_migration_flag():
    """마이그레이션 취소 플래그 파일 제거"""
    try:
        if CANCEL_FLAG_PATH.is_file():
            CANCEL_FLAG_PATH.unlink()
    except Exception:
        pass


def _batch_copy_files(
    pairs: List[Tuple[Path, Path]],
    overwrite: bool = False,
    rclone_remote: Optional[str] = None,
    rclone_mount_base: Optional[str] = None,
    sub_progress_cb: Optional[Callable[[str, str], None]] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    (src_path, dst_path) 목록을 받아 대상 디렉토리별로 그룹화하여
    rclone copy --files-from을 통한 일괄 서버사이드 복사를 수행하고,
    실패한 파일은 로컬 shutil.copy2 폴백을 진행합니다.
    
    Returns:
        (copied_count, failed_items)
    """
    if not pairs:
        return 0, []

    # 1. 파일 검증 및 크기 일치 건너뛰기 필터링
    valid_pairs: List[Tuple[Path, Path]] = []
    skipped_count = 0
    failed_items: List[Dict[str, Any]] = []

    for src_p, dst_p in pairs:
        try:
            if not _path_within(src_p, SOURCE_ROOT):
                failed_items.append({"src": str(src_p), "dst": str(dst_p), "error": "source path outside managed root"})
                continue
            if not _path_within(dst_p, TARGET_ROMM_ROOT):
                failed_items.append({"src": str(src_p), "dst": str(dst_p), "error": "target path outside RomM root"})
                continue
            if not src_p.is_file():
                failed_items.append({"src": str(src_p), "dst": str(dst_p), "error": "source file not found"})
                continue
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            if dst_p.exists() and not overwrite and dst_p.stat().st_size == src_p.stat().st_size:
                skipped_count += 1
                continue
            valid_pairs.append((src_p, dst_p))
        except Exception as ex:
            failed_items.append({"src": str(src_p), "dst": str(dst_p), "error": str(ex)})

    if not valid_pairs:
        return skipped_count, failed_items

    # 2. rclone 원격 일괄 복사 시도
    remaining_pairs: List[Tuple[Path, Path]] = list(valid_pairs)
    rclone_success_count = 0
    remote_batch_errors: Dict[Tuple[str, str], str] = {}

    if rclone_remote:
        try:
            import sys
            if "/app" not in sys.path:
                sys.path.insert(0, "/app")
            from utils.drive_helper import get_rclone_relative_path
            from utils.rclone_gdrive_copy import _run_rclone

            # (src_parent_rel, dst_dir_rel) -> list of (src_name, src_p, dst_p)
            grouped: Dict[Tuple[str, str], List[Tuple[str, Path, Path]]] = {}
            for src_p, dst_p in valid_pairs:
                src_rel = get_rclone_relative_path(str(src_p.resolve()))
                dst_rel = get_rclone_relative_path(str(dst_p.resolve()))
                if src_rel and dst_rel and src_rel != "." and dst_rel != ".":
                    src_parent_rel = os.path.dirname(src_rel)
                    dst_dir_rel = os.path.dirname(dst_rel)
                    grouped.setdefault((src_parent_rel, dst_dir_rel), []).append((src_p.name, src_p, dst_p))
                else:
                    remote_batch_errors[(str(src_p), str(dst_p))] = "rclone 상대 경로 계산 실패"

            total_sub_batches = len(grouped)
            curr_batch_idx = 0
            for (src_parent_rel, dst_dir_rel), file_tuples in grouped.items():
                if is_migration_cancelled():
                    break
                curr_batch_idx += 1
                if sub_progress_cb:
                    sub_progress_cb(
                        f"서버사이드 배치 복사 중 ({curr_batch_idx}/{total_sub_batches})",
                        f"경로: {src_parent_rel} -> {dst_dir_rel} ({len(file_tuples)}개 파일)",
                    )
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
                    for name, _, _ in file_tuples:
                        tf.write(f"{name}\n")
                    tf_path = tf.name

                try:
                    src_remote_dir = f"{rclone_remote}:{src_parent_rel}" if src_parent_rel else f"{rclone_remote}:"
                    dst_remote_dir = f"{rclone_remote}:{dst_dir_rel}" if dst_dir_rel else f"{rclone_remote}:"
                    copy_args = [
                        "copy",
                        src_remote_dir,
                        dst_remote_dir,
                        "--files-from", tf_path,
                        "--drive-server-side-across-configs",
                        "--fast-list",
                    ]
                    code, _, stderr = _run_rclone(copy_args, timeout=120)
                    if code == 0:
                        for _, src_p, dst_p in file_tuples:
                            if (src_p, dst_p) in remaining_pairs:
                                remaining_pairs.remove((src_p, dst_p))
                                rclone_success_count += 1
                    else:
                        err_text = stderr.decode('utf-8', 'replace').strip() or 'unknown rclone error'
                        logger.debug(f"Batch rclone copy failed ({src_parent_rel} -> {dst_dir_rel}): {err_text}")
                        for _, src_p, dst_p in file_tuples:
                            remote_batch_errors[(str(src_p), str(dst_p))] = f"서버사이드 배치 복사 실패: {err_text}"
                except Exception as ex:
                    logger.debug(f"Batch rclone copy error: {ex}")
                    for _, src_p, dst_p in file_tuples:
                        remote_batch_errors[(str(src_p), str(dst_p))] = f"서버사이드 배치 복사 예외: {ex}"
                finally:
                    if os.path.exists(tf_path):
                        try:
                            os.unlink(tf_path)
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Rclone batch module load/exec error: {e}")
            for src_p, dst_p in remaining_pairs:
                remote_batch_errors[(str(src_p), str(dst_p))] = f"rclone 모듈/실행 준비 실패: {e}"

        # 원격 모드에서는 절대 로컬 FUSE 복사 폴백으로 떨어지지 않는다.
        for src_p, dst_p in remaining_pairs:
            failed_items.append({
                "src": str(src_p),
                "dst": str(dst_p),
                "error": remote_batch_errors.get((str(src_p), str(dst_p)), "서버사이드 복사 미완료 (로컬 폴백 비활성화)"),
            })
        total_copied = skipped_count + rclone_success_count
        return total_copied, failed_items

    # 3. 로컬 모드에서만 shutil.copy2 폴백 허용
    local_success_count = 0
    for src_p, dst_p in remaining_pairs:
        if is_migration_cancelled():
            break
        try:
            shutil.copy2(src_p, dst_p)
            local_success_count += 1
        except Exception as e:
            failed_items.append({"src": str(src_p), "dst": str(dst_p), "error": str(e)})

    total_copied = skipped_count + rclone_success_count + local_success_count
    return total_copied, failed_items


def _safe_copy_file(
    src: str | Path,
    dst: str | Path,
    overwrite: bool = False,
    rclone_remote: Optional[str] = None,
    rclone_mount_base: Optional[str] = None,
) -> bool:
    """단일 파일 단독 복사 (하위 호환용)"""
    copied, failed = _batch_copy_files([(Path(src), Path(dst))], overwrite=overwrite, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base)
    return copied > 0 and len(failed) == 0


def _get_target_db_path(plugin_data_dir: Optional[str] = None) -> Optional[Path]:
    """gba.db SQLite 데이터베이스 경로 탐색"""
    candidates = [
        plugin_data_dir,
        os.environ.get("PLUGIN_DATA_DIR"),
        str(ROOT_DIR.parent.parent / "data" / "bookoasis_gamebooks"),
        "/app/plugins/data/bookoasis_gamebooks",
    ]
    for cand in candidates:
        if cand:
            p = Path(cand) / "gba.db"
            if p.is_file() or p.parent.is_dir():
                return p
    return None


def backup_db_before_migration(plugin_data_dir: Optional[str] = None) -> Optional[Path]:
    """마이그레이션 실행 직전 gba.db를 timestamp 백업본으로 안전 복제"""
    db_path = _get_target_db_path(plugin_data_dir)
    if not db_path or not db_path.is_file():
        logger.info(f"No existing gba.db found to backup at {db_path}")
        return None
    try:
        ts = int(time.time())
        bak_path = db_path.parent / f"gba.db.bak_migration_{ts}"
        shutil.copy2(str(db_path), str(bak_path))
        logger.info(f"Created pre-migration DB backup: {bak_path}")
        return bak_path
    except Exception as e:
        logger.warning(f"Failed to backup gba.db before migration: {e}")
        return None


def sync_manifest_to_db(
    manifest: List[Dict[str, Any]],
    bios_map: Optional[List[Dict[str, Any]]] = None,
    plugin_data_dir: Optional[str] = None,
) -> int:
    """
    마이그레이션 완료 후, manifest의 신규 RomM 경로(target_paths[0]) 및
    감지된 platform/core 정보를 gba.db에 원자적으로 일괄 동기화합니다.
    """
    db_path = _get_target_db_path(plugin_data_dir)
    if not db_path or not db_path.is_file():
        logger.warning(f"Cannot sync manifest to DB: gba.db not found at {db_path}")
        return 0

    import hashlib
    import re

    def _sanitize_id(text: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", text)
        h = hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
        return f"{clean[:40]}_{h}"

    updated_count = 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT id, filename, file_path, core, platform FROM games")
        rows = cur.fetchall()
        existing_by_src: Dict[str, Dict[str, Any]] = {}
        existing_by_fn: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            d = dict(r)
            fp = d.get("file_path") or ""
            fn = d.get("filename") or ""
            if fp:
                existing_by_src[os.path.normpath(fp)] = d
            if fn:
                existing_by_fn[fn.lower()] = d

        cur.execute("BEGIN TRANSACTION")
        for item in manifest:
            src_p = item.get("source_path") or ""
            target_paths = item.get("target_paths") or []
            if not target_paths:
                continue
            new_file_path = str(target_paths[0])
            new_filename = Path(new_file_path).name

            target_slug = item.get("target_platform_slug") or ""
            det_plat = item.get("detected_platform") or target_slug.upper()
            det_core = item.get("detected_core") or target_slug

            # 기존 레코드 찾기
            rec = None
            if src_p:
                rec = existing_by_src.get(os.path.normpath(src_p))
            if not rec:
                rec = existing_by_fn.get(Path(src_p or new_file_path).name.lower())

            if rec:
                old_id = rec["id"]
                cur.execute(
                    """UPDATE games 
                          SET file_path = ?, filename = ?, platform = ?, core = ?
                          WHERE id = ?""",
                    (new_file_path, new_filename, det_plat, det_core, old_id),
                )
                updated_count += 1
            else:
                # 신규 등록
                new_id = _sanitize_id(f"library_{new_filename}")
                cur.execute(
                    """INSERT OR REPLACE INTO games (id, filename, file_path, title, platform, core, size_bytes, mtime)
                          VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                    (new_id, new_filename, new_file_path, item.get("title_guess") or Path(new_file_path).stem, det_plat, det_core, time.time()),
                )
                updated_count += 1

        conn.commit()
        conn.close()
        logger.info(f"Successfully synced {updated_count} manifest entries to gba.db")
    except Exception as e:
        logger.error(f"Failed to sync manifest to gba.db: {e}")

    return updated_count


def rollback_migration(
    manifest_path: Optional[Path] = None,
    bios_map_path: Optional[Path] = None,
    plugin_data_dir: Optional[str] = None,
    target_romm_root: Optional[Path] = None,
    rclone_remote: Optional[str] = None,
) -> Dict[str, Any]:
    """
    RomM 마이그레이션 결과 100% 원복(Rollback):
    1. manifest 및 bios_map의 대상 파일(target_paths) 안전 삭제 (소스 원본은 보존)
    2. rclone 원격 마운트 환경인 경우 rclone purge / delete 로 신속하게 원격 타겟 정리
    3. 생성된 빈 RomM 디렉터리 정리
    4. gba.db 백업본(bak_migration_*)이 존재하면 최신 백업본으로 복원
    """
    manifest_f = manifest_path or MANIFEST_PATH
    bios_map_f = bios_map_path or BIOS_MAP_PATH
    romm_root = target_romm_root or TARGET_ROMM_ROOT

    deleted_files = 0
    errors: List[str] = []
    if not _is_expected_romm_root(romm_root):
        return {
            "success": False,
            "deleted_files": 0,
            "db_restored": False,
            "errors": [f"Refusing rollback for unexpected RomM root: {romm_root}"],
        }

    # 1. rclone 원격 정리 우선 시도 (고속 서버사이드 purge)
    if rclone_remote:
        try:
            import sys
            if "/app" not in sys.path:
                sys.path.insert(0, "/app")
            from utils.drive_helper import get_rclone_relative_path
            from utils.rclone_gdrive_copy import _run_rclone

            rel_romm_root = get_rclone_relative_path(str(romm_root))
            normalized_rel = str(rel_romm_root or "").replace("\\", "/").strip("/")
            if normalized_rel and normalized_rel != "." and normalized_rel.endswith("/romm_library") and ".." not in normalized_rel.split("/"):
                remote_target = f"{rclone_remote}:{normalized_rel}"
                logger.info(f"Purging remote RomM directory via rclone: {remote_target}")
                code, _, stderr = _run_rclone(["purge", remote_target], timeout=120)
                if code == 0:
                    logger.info("Successfully purged remote RomM directory via rclone")
                    deleted_files += 1
                else:
                    err_msg = stderr.decode("utf-8", "replace").strip()
                    logger.debug(f"rclone purge failed ({remote_target}): {err_msg}")
        except Exception as r_err:
            logger.debug(f"rclone purge exception: {r_err}")

    # 2. 로컬 / FUSE 타겟 파일 정리
    targets_to_delete: List[Path] = []
    if manifest_f.is_file():
        try:
            with open(manifest_f, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for item in manifest:
                for tp in item.get("target_paths", []):
                    if _path_within(tp, romm_root):
                        targets_to_delete.append(Path(tp))
                    else:
                        errors.append(f"Skipped rollback target outside RomM root: {tp}")
        except Exception as e:
            errors.append(f"Failed to read manifest for rollback: {e}")

    if bios_map_f.is_file():
        try:
            with open(bios_map_f, "r", encoding="utf-8") as f:
                bios_map = json.load(f)
            for item in bios_map:
                tb = item.get("target_bios")
                if tb:
                    if _path_within(tb, romm_root):
                        targets_to_delete.append(Path(tb))
                    else:
                        errors.append(f"Skipped BIOS rollback target outside RomM root: {tb}")
        except Exception as e:
            errors.append(f"Failed to read bios_map for rollback: {e}")

    for p in targets_to_delete:
        try:
            if p.is_file():
                p.unlink()
                deleted_files += 1
        except Exception as ex:
            errors.append(f"Failed to delete rollback target {p}: {ex}")

    # 3. 빈 디렉터리 정리
    try:
        if romm_root.is_dir():
            for root, dirs, files in os.walk(str(romm_root), topdown=False):
                for d in dirs:
                    d_path = Path(root) / d
                    try:
                        if d_path.is_dir() and not any(d_path.iterdir()):
                            d_path.rmdir()
                    except Exception:
                        pass
            if romm_root.is_dir() and not any(romm_root.iterdir()):
                try:
                    romm_root.rmdir()
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Clean empty dirs warning: {e}")

    # 4. DB 복원 (최신 gba.db.bak_migration_* 찾아서 복원)
    db_restored = False
    db_path = _get_target_db_path(plugin_data_dir)
    if db_path and db_path.parent.is_dir():
        try:
            pattern = str(db_path.parent / "gba.db.bak_migration_*")
            bak_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
            if bak_files:
                latest_bak = bak_files[0]
                shutil.copy2(latest_bak, str(db_path))
                db_restored = True
                logger.info(f"Restored gba.db from backup: {latest_bak}")
        except Exception as ex:
            errors.append(f"Failed to restore gba.db: {ex}")

    # 5. 상태 플래그 및 매니페스트 정리
    try:
        clear_cancel_migration_flag()
    except Exception:
        pass

    logger.info(f"Rollback completed. Deleted {deleted_files} files, DB restored: {db_restored}")
    return {
        "success": True,
        "deleted_files": deleted_files,
        "db_restored": db_restored,
        "errors": errors,
    }


def execute_migration_plan(
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    manifest_path: Optional[Path] = None,
    bios_map_path: Optional[Path] = None,
    cover_map_path: Optional[Path] = None,
    rclone_remote: Optional[str] = None,
    rclone_mount_base: Optional[str] = None,
    plugin_data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    manifest_f = manifest_path or MANIFEST_PATH
    bios_map_f = bios_map_path or BIOS_MAP_PATH
    cover_map_f = cover_map_path or COVER_MAP_PATH

    if not manifest_f.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_f}")

    with open(manifest_f, "r", encoding="utf-8") as f:
        manifest: List[Dict[str, Any]] = json.load(f)

    bios_map: List[Dict[str, Any]] = []
    if bios_map_f.is_file():
        with open(bios_map_f, "r", encoding="utf-8") as f:
            bios_map = json.load(f)

    # 커버 아트는 마이그레이션 대상에서 제외 (사용자 요청: 이름 변경 미지원으로 인한 실패 다발)
    cover_map: List[Dict[str, Any]] = []

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
                "is_rclone_mode": bool(rclone_remote),
                "rclone_remote": rclone_remote or "",
                "rclone_mount_base": rclone_mount_base or "",
            })

    emit("start", "마이그레이션 시작 준비", f"총 {total_steps}건 계획 로드 완료 (서버사이드 일괄 복사 모드)")

    # 1. 기종(플랫폼)별 ROM/번들 일괄 그룹 복사
    from collections import defaultdict
    platform_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in manifest:
        slug = item.get("target_platform_slug") or "unknown"
        platform_groups[slug].append(item)

    total_platforms = len(platform_groups)
    current_plat_idx = 0

    for slug, items in platform_groups.items():
        if is_migration_cancelled():
            logger.info("Migration cancelled requested by flag file.")
            emit("cancelled", "마이그레이션이 취소되었습니다", "사용자 요청에 의해 마이그레이션이 중단되었습니다.")
            return {
                "success": False,
                "cancelled": True,
                "total_steps": total_steps,
                "copied_rom_files": copied_rom_files,
                "copied_bios_files": copied_bios_files,
                "copied_cover_files": copied_cover_files,
                "failed_count": len(failed_items),
                "failed_items": failed_items[:50],
            }

        current_plat_idx += 1
        pairs_to_copy: List[Tuple[Path, Path]] = []
        descriptors_to_rewrite: List[str] = []

        for item in items:
            src_path = item.get("source_path") or ""
            target_paths = item.get("target_paths") or []
            bundle_files = item.get("bundle_files") or [src_path]
            target_rom_dir = Path(item.get("target_rom_dir") or (TARGET_LIBRARY_DIR / slug / "roms"))

            if len(bundle_files) == 1 and target_paths:
                pairs_to_copy.append((Path(src_path), Path(target_paths[0])))
            else:
                for b_file in bundle_files:
                    b_dst = target_rom_dir / os.path.basename(b_file)
                    pairs_to_copy.append((Path(b_file), b_dst))

            if item.get("rewrite_descriptor") and target_paths:
                descriptors_to_rewrite.append(target_paths[0])

        emit("rom", f"ROM 기종 일괄 복사 중 [{slug}] ({current_plat_idx}/{total_platforms})", f"총 {len(items)}개 타이틀 ({len(pairs_to_copy)}개 파일) 준비 중...")

        def _on_sub_batch(header: str, detail: str):
            emit("rom", f"ROM [{slug}] ({current_plat_idx}/{total_platforms}) - {header}", detail)

        copied, errs = _batch_copy_files(pairs_to_copy, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base, sub_progress_cb=_on_sub_batch)
        copied_rom_files += copied
        if errs:
            failed_items.extend(errs)

        # 디스크립터 경로 패치 (.cue / .gdi)
        for cue_dst in descriptors_to_rewrite:
            try:
                try:
                    from bookoasis_gamebooks import _rewrite_disk_manifest_to_local_paths
                except ImportError:
                    from plugins.metadata.bookoasis_gamebooks.bookoasis_gamebooks import _rewrite_disk_manifest_to_local_paths
                _rewrite_disk_manifest_to_local_paths(cue_dst)
            except Exception as ex:
                logger.warning(f"Descriptor rewrite failed on {cue_dst}: {ex}")

        completed_steps += len(items)
        time.sleep(0.01)

    # 2. BIOS 파일 일괄 복사
    if bios_map:
        if is_migration_cancelled():
            logger.info("Migration cancelled requested by flag file.")
            emit("cancelled", "마이그레이션이 취소되었습니다", "사용자 요청에 의해 마이그레이션이 중단되었습니다.")
            return {
                "success": False,
                "cancelled": True,
                "total_steps": total_steps,
                "copied_rom_files": copied_rom_files,
                "copied_bios_files": copied_bios_files,
                "copied_cover_files": copied_cover_files,
                "failed_count": len(failed_items),
                "failed_items": failed_items[:50],
            }

        emit("bios", "BIOS 파일 일괄 복사 중", f"총 {len(bios_map)}건 대상")
        bios_pairs: List[Tuple[Path, Path]] = []
        for item in bios_map:
            src_path = item.get("source_bios") or item.get("source_path") or ""
            target_path = item.get("target_path") or ""
            if src_path and target_path:
                bios_pairs.append((Path(src_path), Path(target_path)))

        copied, errs = _batch_copy_files(bios_pairs, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base)
        copied_bios_files += copied
        if errs:
            failed_items.extend(errs)
        completed_steps += len(bios_map)

    # 3. Cover 아트 일괄 복사
    if cover_map:
        if is_migration_cancelled():
            logger.info("Migration cancelled requested by flag file.")
            emit("cancelled", "마이그레이션이 취소되었습니다", "사용자 요청에 의해 마이그레이션이 중단되었습니다.")
            return {
                "success": False,
                "cancelled": True,
                "total_steps": total_steps,
                "copied_rom_files": copied_rom_files,
                "copied_bios_files": copied_bios_files,
                "copied_cover_files": copied_cover_files,
                "failed_count": len(failed_items),
                "failed_items": failed_items[:50],
            }

        emit("cover", "커버 아트 일괄 복사 중", f"총 {len(cover_map)}건 대상")
        cover_pairs: List[Tuple[Path, Path]] = []
        for item in cover_map:
            src_cover = item.get("source_cover") or ""
            target_resource = item.get("target_resource_dir") or ""
            target_cover_path = item.get("target_cover_path") or ""
            target_asset = item.get("target_asset_path") or ""

            if target_cover_path and src_cover:
                cover_pairs.append((Path(src_cover), Path(target_cover_path)))
            elif target_resource and src_cover:
                c_ext = os.path.splitext(src_cover)[1].lower() or ".png"
                cover_pairs.append((Path(src_cover), Path(target_resource) / f"cover{c_ext}"))
            if target_asset and src_cover:
                cover_pairs.append((Path(src_cover), Path(target_asset)))

        copied, errs = _batch_copy_files(cover_pairs, rclone_remote=rclone_remote, rclone_mount_base=rclone_mount_base)
        copied_cover_files += copied
        if errs:
            failed_items.extend(errs)
        completed_steps += len(cover_map)

    # 4. gba.db에 마이그레이션된 신규 경로 동기화
    db_synced_count = 0
    if len(failed_items) == 0:
        emit("syncing_db", "데이터베이스 메타데이터 동기화 중...", f"총 {len(manifest)}건 경로 갱신")
        db_synced_count = sync_manifest_to_db(manifest, bios_map, plugin_data_dir=plugin_data_dir)

    emit("completed", "마이그레이션 완료", f"ROM: {copied_rom_files}, BIOS: {copied_bios_files}, DB동기화: {db_synced_count}건")

    return {
        "success": len(failed_items) == 0,
        "total_steps": total_steps,
        "copied_rom_files": copied_rom_files,
        "copied_bios_files": copied_bios_files,
        "copied_cover_files": copied_cover_files,
        "db_synced_count": db_synced_count,
        "failed_count": len(failed_items),
        "failed_items": failed_items[:50],
        "is_rclone_mode": bool(rclone_remote),
        "rclone_remote": rclone_remote or "",
        "rclone_mount_base": rclone_mount_base or "",
    }


if __name__ == "__main__":
    def cli_progress(p: Dict[str, Any]):
        print(f"[{p['percent']}%] ({p['phase']}) {p['current_item']} - {p['details']}", flush=True)

    rclone_remote = os.environ.get("RCLONE_REMOTE", "google_drive")
    rclone_mount = os.environ.get("RCLONE_MOUNT_BASE", "/mnt/gdrive")

    res = execute_migration_plan(
        progress_cb=cli_progress,
        rclone_remote=rclone_remote,
        rclone_mount_base=rclone_mount,
    )
    print("\nResult:", json.dumps(res, ensure_ascii=False, indent=2))
