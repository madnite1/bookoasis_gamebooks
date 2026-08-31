# -*- coding: utf-8 -*-
"""기존 rom_analyzer.db 공개 API 호환 래퍼.

실제 SQLite 접근은 rom_database.MetadataRepository가 담당한다.
"""

from typing import Any, Dict, Optional

from .database_context import get_active_database


def query_arcade_romset(rom_name: str) -> Optional[Dict[str, Any]]:
    """아케이드 롬셋명 exact lookup."""
    return get_active_database().metadata.find_arcade_romset(rom_name)


def query_disc_serial(serial: str) -> Optional[Dict[str, Any]]:
    """디스크/카트리지 시리얼로 공식 타이틀/기종 조회."""
    return get_active_database().metadata.find_disc_serial(serial)


def query_bios_manifest(system_id: str) -> Optional[Dict[str, Any]]:
    """기종별 BIOS 요구사항 조회."""
    return get_active_database().metadata.find_bios_manifest(system_id)
