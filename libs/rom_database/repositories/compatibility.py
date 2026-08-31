# -*- coding: utf-8 -*-
"""MAME2003 계열 원시 게임 호환성 조회."""

from pathlib import Path
from typing import Dict, Optional, Union

from ..connection import open_readonly
from ..models import CoreCompatibilityRecord

PathLike = Union[str, Path]

CORE_SOURCE_URLS = {
    "mame2003": "https://buildbot.libretro.com/compatibility_lists/cores/mame2003/mame2003.html",
    "mame2003_plus": "https://buildbot.libretro.com/compatibility_lists/cores/mame2003-plus/mame2003-plus.html",
}


class CompatibilityRepository:
    def __init__(self, db_path: PathLike):
        self.db_path = Path(db_path)

    def is_available(self) -> bool:
        return self.db_path.is_file()

    def find(self, rom_name: str, core_id: str) -> Optional[CoreCompatibilityRecord]:
        rom = (rom_name or "").lower().strip()
        core = (core_id or "").lower().strip()
        if not rom or core not in CORE_SOURCE_URLS or not self.is_available():
            return None
        connection = open_readonly(self.db_path)
        if connection is None:
            return None
        try:
            row = connection.execute(
                """SELECT core_id, rom_name, description, driver_status,
                          color_status, sound_status, graphics_status,
                          samples, bios_required
                   FROM compatibility
                   WHERE core_id=? AND rom_name=?""",
                (core, rom),
            ).fetchone()
        except Exception:
            return None
        finally:
            connection.close()
        if not row:
            return None
        return CoreCompatibilityRecord(
            core_id=row["core_id"],
            rom_name=row["rom_name"],
            description=row["description"] or "",
            driver_status=(row["driver_status"] or "unknown").strip().lower(),
            color_status=row["color_status"] or None,
            sound_status=row["sound_status"] or None,
            graphics_status=row["graphics_status"] or None,
            samples=row["samples"] or None,
            bios_required=bool(row["bios_required"]),
            source_url=CORE_SOURCE_URLS.get(core),
        )

    def find_for_rom(self, rom_name: str) -> Dict[str, CoreCompatibilityRecord]:
        result: Dict[str, CoreCompatibilityRecord] = {}
        for core_id in CORE_SOURCE_URLS:
            record = self.find(rom_name, core_id)
            if record:
                result[core_id] = record
        return result
