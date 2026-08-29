# -*- coding: utf-8 -*-
"""
ROM Analyzer 내장 SQLite 데이터베이스 조회 매니저.
아케이드 롬셋, 기판, 바이오스, 디스크 시리얼 번호 색인 검색.
"""

import os
import json
import sqlite3
import logging
from typing import Optional, Dict, Any, List

_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "rom_metadata.db")
_MEM_CACHE: Dict[str, Any] = {}
logger = logging.getLogger(__name__)


def _get_connection():
    if not os.path.exists(_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        logger.debug("metadata DB connection failed for %s: %s", _DB_PATH, exc, exc_info=True)
        return None


def query_arcade_romset(rom_name: str) -> Optional[Dict[str, Any]]:
    """아케이드 롬셋명 exact lookup. Clone 관계는 DB/DAT에 명시된 값만 신뢰한다."""
    if not rom_name:
        return None
    key = rom_name.lower().strip()
    cache_key = f"arc:{key}"
    if cache_key in _MEM_CACHE:
        return _MEM_CACHE[cache_key]

    conn = _get_connection()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        # 1. 완전 일치 조회
        cur.execute("SELECT * FROM arcade_romsets WHERE rom_name = ?", (key,))
        row = cur.fetchone()
        if row:
            res = {
                "rom_name": row["rom_name"],
                "title": row["title"],
                "board": row["board"],
                "parent": row["parent_rom"],
                "is_clone": bool(row["is_clone"]),
                "is_bios": bool(row["is_bios"]),
                "is_device": bool(row["is_device"]),
                "bios": json.loads(row["required_bios"] or "[]"),
                "chd": bool(row["needs_chd"]),
                "chd_name": row["chd_name"],
                "year": row["year"],
                "manufacturer": row["manufacturer"],
                "cores": json.loads(row["recommended_cores"] or '["fbneo", "mame"]')
            }
            _MEM_CACHE[cache_key] = res
            return res

    except Exception as exc:
        logger.debug("arcade romset query failed for %s: %s", key, exc)
    finally:
        conn.close()

    return None


def query_disc_serial(serial: str) -> Optional[Dict[str, Any]]:
    """디스크/카트리지 시리얼(SLUS-00594, NUS-CZLE 등)로 공식 타이틀/기종 조회"""
    if not serial:
        return None
    key = serial.strip().upper()
    cache_key = f"ser:{key}"
    if cache_key in _MEM_CACHE:
        return _MEM_CACHE[cache_key]

    conn = _get_connection()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        # 1. 완전 일치
        cur.execute("SELECT * FROM disc_serials WHERE serial = ?", (key,))
        row = cur.fetchone()
        if not row:
            # 2. 하이픈 없는 버전 시도
            key_clean = key.replace("-", "").replace("_", "")
            cur.execute("SELECT * FROM disc_serials WHERE REPLACE(REPLACE(serial, '-', ''), '_', '') = ?", (key_clean,))
            row = cur.fetchone()

        if row:
            res = {
                "serial": row["serial"],
                "system_id": row["system_id"],
                "title": row["title"],
                "region": row["region"],
                "manufacturer": row["manufacturer"]
            }
            _MEM_CACHE[cache_key] = res
            return res
    except Exception as exc:
        logger.debug("disc serial query failed for %s: %s", key, exc)
    finally:
        conn.close()

    return None


def query_bios_manifest(system_id: str) -> Optional[Dict[str, Any]]:
    """기종별 바이오스 요구사항 조회"""
    if not system_id:
        return None
    key = system_id.lower().strip()
    cache_key = f"bio:{key}"
    if cache_key in _MEM_CACHE:
        return _MEM_CACHE[cache_key]

    conn = _get_connection()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM bios_manifest WHERE system_id = ?", (key,))
        row = cur.fetchone()
        if row:
            res = {
                "system_id": row["system_id"],
                "system_name": row["system_name"],
                "mandatory": bool(row["mandatory"]),
                "bios_files": json.loads(row["bios_files"] or "[]"),
                "description": row["description"]
            }
            _MEM_CACHE[cache_key] = res
            return res
    except Exception as exc:
        logger.debug("bios manifest query failed for %s: %s", key, exc)
    finally:
        conn.close()

    return None
