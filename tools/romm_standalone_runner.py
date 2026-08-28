#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/romm_standalone_runner.py

독립 백그라운드 프로세스로 실행되어 RomM 마이그레이션(계획 수립 및 복사 적용)을 수행합니다.
웹 서버(Gunicorn) 프로세스와 완전히 격리되어 웹 요청 및 응답에 전혀 영향을 주지 않습니다.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

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
    CANCEL_FLAG_PATH,
    MANIFEST_PATH,
    STATUS_PATH,
)
from .romm_migration_plan import build_dry_run_plan
from .romm_migration_apply import (
    backup_db_before_migration,
    clear_cancel_migration_flag,
    execute_migration_plan,
    is_migration_cancelled,
    rollback_migration,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (RomM-Runner) %(message)s",
)
logger = logging.getLogger("romm_standalone_runner")


def _write_status_atomically(status_dict: Dict[str, Any]) -> None:
    """임시 파일을 거쳐 상태 파일을 atomic rename으로 기록 (0바이트 손상 방지)"""
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        status_copy = dict(status_dict)
        status_copy["updated_at"] = time.time()
        
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(STATUS_PATH.parent),
            delete=False,
            prefix=".tmp_status_",
        ) as tf:
            json.dump(status_copy, tf, ensure_ascii=False)
            temp_name = tf.name

        os.replace(temp_name, STATUS_PATH)
    except Exception as ex:
        logger.warning(f"Failed to atomically write status file: {ex}")


def run_standalone_migration(
    force: bool = False,
    rclone_remote: str | None = None,
    rclone_mount_base: str | None = None,
    plugin_data_dir: str | None = None,
) -> Dict[str, Any]:
    """독립 프로세스 내에서 전체 마이그레이션 라이프사이클 실행"""
    pid = os.getpid()
    logger.info(f"Starting standalone migration runner (PID={pid}, force={force})")
    
    # 시작 전 취소 플래그 초기화
    clear_cancel_migration_flag()

    status: Dict[str, Any] = {
        "is_running": True,
        "state": "running",
        "phase": "starting",
        "percent": 0,
        "current": 0,
        "total": 0,
        "current_item": "마이그레이션 백그라운드 프로세스 준비 중...",
        "details": "계획 분석 시작",
        "copied_rom_files": 0,
        "copied_bios_files": 0,
        "copied_cover_files": 0,
        "failed_count": 0,
        "error": "",
        "launcher_pid": pid,
    }
    _write_status_atomically(status)

    try:
        # 1. 계획 파일(manifest) 확인 및 생성
        if not MANIFEST_PATH.is_file() or force:
            logger.info("Building migration plan (manifest)...")
            status.update({
                "phase": "planning",
                "current_item": "최신 파일 및 메타데이터 계획 분석 중...",
                "details": "소스 ROM, BIOS, 커버 인덱스 구성 중",
            })
            _write_status_atomically(status)

            if is_migration_cancelled():
                logger.info("Cancelled during planning phase")
                status.update({
                    "is_running": False,
                    "state": "cancelled",
                    "phase": "cancelled",
                    "current_item": "마이그레이션이 취소되었습니다",
                    "details": "계획 수립 도중 사용자에 의해 취소됨",
                })
                _write_status_atomically(status)
                return status

            def _plan_cb(p: Dict[str, Any]):
                status.update({
                    "is_running": True,
                    "state": "running",
                    "phase": p.get("phase", "planning"),
                    "percent": p.get("percent", 0),
                    "current": p.get("current", 0),
                    "total": p.get("total", 0),
                    "current_item": p.get("current_item", ""),
                    "details": p.get("details", ""),
                    "launcher_pid": pid,
                })
                _write_status_atomically(status)

            try:
                build_dry_run_plan(progress_cb=_plan_cb)
            except InterruptedError:
                logger.info("Cancelled during build_dry_run_plan")
                status.update({
                    "is_running": False,
                    "state": "cancelled",
                    "phase": "cancelled",
                    "current_item": "마이그레이션이 취소되었습니다",
                    "details": "계획 수립 도중 사용자에 의해 취소됨",
                })
                _write_status_atomically(status)
                return status

        # 2. 실행 콜백
        def _apply_cb(p: Dict[str, Any]):
            status.update({
                "is_running": p.get("phase") not in ("completed", "failed", "cancelled"),
                "state": p.get("phase") if p.get("phase") in ("completed", "failed", "cancelled") else "running",
                "phase": p.get("phase", "running"),
                "percent": p.get("percent", 0),
                "current": p.get("current", 0),
                "total": p.get("total", 0),
                "current_item": p.get("current_item", ""),
                "details": p.get("details", ""),
                "copied_rom_files": p.get("copied_rom_files", 0),
                "copied_bios_files": p.get("copied_bios_files", 0),
                "copied_cover_files": p.get("copied_cover_files", 0),
                "failed_count": p.get("failed_count", 0),
                "launcher_pid": pid,
            })
            _write_status_atomically(status)

        _apply_cb({
            "phase": "running",
            "percent": 0,
            "current": 0,
            "total": 0,
            "current_item": "ROM 및 리소스 복사 시작 중...",
            "details": "계획 수립 완료, 파일 전송 단계 진입",
        })

        # 3. 마이그레이션 실행 전 DB 백업 생성
        backup_db_before_migration(plugin_data_dir=plugin_data_dir)

        # 4. 복사 및 디스크립터 패치, DB 동기화 실행
        logger.info("Executing migration plan...")
        res = execute_migration_plan(
            progress_cb=_apply_cb,
            rclone_remote=rclone_remote,
            rclone_mount_base=rclone_mount_base,
            plugin_data_dir=plugin_data_dir,
        )

        if res.get("cancelled"):
            logger.info("Migration was cancelled by user.")
            status.update({
                "is_running": False,
                "state": "cancelled",
                "phase": "cancelled",
                "current_item": "마이그레이션이 취소되었습니다",
                "details": "복사 작업 중단됨",
            })
            _write_status_atomically(status)
            return status

        if res.get("success"):
            logger.info("Migration completed successfully!")
            status.update({
                "is_running": False,
                "state": "completed",
                "phase": "completed",
                "percent": 100,
                "current_item": "마이그레이션 완료",
                "details": f"ROM {res.get('copied_rom_files', 0)}개, BIOS {res.get('copied_bios_files', 0)}개, Cover {res.get('copied_cover_files', 0)}개 복사 완료",
            })
            _write_status_atomically(status)
            return status
        else:
            err_msg = f"마이그레이션 중 일부 오류 발생 (실패: {res.get('failed_count', 0)}건)"
            logger.warning(err_msg)
            status.update({
                "is_running": False,
                "state": "failed",
                "phase": "failed",
                "percent": 100,
                "error": err_msg,
                "details": err_msg,
                "failed_count": res.get("failed_count", 0),
                "failed_items": res.get("failed_items", []),
                "copied_rom_files": res.get("copied_rom_files", 0),
                "copied_bios_files": res.get("copied_bios_files", 0),
                "copied_cover_files": res.get("copied_cover_files", 0),
            })
            _write_status_atomically(status)
            return status

    except Exception as ex:
        logger.error(f"Fatal migration error: {ex}", exc_info=True)
        status.update({
            "is_running": False,
            "state": "failed",
            "phase": "failed",
            "error": str(ex),
            "details": str(ex),
        })
        _write_status_atomically(status)
        return status


if __name__ == "__main__":
    force_run = "--force" in sys.argv
    rclone_rem = os.environ.get("RCLONE_REMOTE")
    rclone_mnt = os.environ.get("RCLONE_MOUNT_BASE")
    p_data_dir = os.environ.get("PLUGIN_DATA_DIR")

    run_standalone_migration(
        force=force_run,
        rclone_remote=rclone_rem,
        rclone_mount_base=rclone_mnt,
        plugin_data_dir=p_data_dir,
    )
