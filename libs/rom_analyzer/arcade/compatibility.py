# -*- coding: utf-8 -*-
"""MAME2003 계열 게임별 실행 호환성 조회.

런타임에서는 네트워크를 사용하지 않고 패키지에 포함된 SQLite 스냅샷만 조회한다.
DB는 프로젝트 루트의 ``build_mame_compatibility_db.py``로 공식 Libretro
호환성 표에서 갱신할 수 있다.
"""

from functools import lru_cache
import os
import sqlite3
from typing import Dict, Optional

from ..models import ArcadeCoreCompatibility


_COMPAT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "mame_compatibility.db"
)

CORE_SOURCE_URLS = {
    "mame2003": "https://buildbot.libretro.com/compatibility_lists/cores/mame2003/mame2003.html",
    "mame2003_plus": "https://buildbot.libretro.com/compatibility_lists/cores/mame2003-plus/mame2003-plus.html",
}

_WORKING_DRIVER_STATUS = "good"


class ArcadeCompatibilityManager:
    """패키지 내 MAME2003 호환성 스냅샷 조회기."""

    @classmethod
    def is_available(cls) -> bool:
        return os.path.isfile(_COMPAT_DB_PATH)

    @classmethod
    def _connect(cls):
        con = sqlite3.connect(f"file:{_COMPAT_DB_PATH}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        return con

    @classmethod
    @lru_cache(maxsize=4096)
    def get(cls, rom_name: str, core_id: str) -> Optional[ArcadeCoreCompatibility]:
        rom = (rom_name or "").lower().strip()
        core = (core_id or "").lower().strip()
        if not rom or core not in CORE_SOURCE_URLS or not cls.is_available():
            return None
        try:
            with cls._connect() as con:
                row = con.execute(
                    """SELECT core_id, rom_name, description, driver_status,
                              color_status, sound_status, graphics_status,
                              samples, bios_required
                       FROM compatibility
                       WHERE core_id=? AND rom_name=?""",
                    (core, rom),
                ).fetchone()
        except (OSError, sqlite3.Error):
            return None
        if not row:
            return None
        driver_status = (row["driver_status"] or "unknown").strip().lower()
        return ArcadeCoreCompatibility(
            core_id=row["core_id"],
            rom_name=row["rom_name"],
            description=row["description"] or "",
            supported=driver_status == _WORKING_DRIVER_STATUS,
            driver_status=driver_status,
            color_status=row["color_status"] or None,
            sound_status=row["sound_status"] or None,
            graphics_status=row["graphics_status"] or None,
            samples=row["samples"] or None,
            bios_required=bool(row["bios_required"]),
            source_url=CORE_SOURCE_URLS.get(core),
        )

    @classmethod
    def get_for_rom(cls, rom_name: str) -> Dict[str, ArcadeCoreCompatibility]:
        result: Dict[str, ArcadeCoreCompatibility] = {}
        for core_id in CORE_SOURCE_URLS:
            info = cls.get(rom_name, core_id)
            if info:
                result[core_id] = info
        return result

    @classmethod
    def clear_cache(cls) -> None:
        cls.get.cache_clear()
