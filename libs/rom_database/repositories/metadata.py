# -*- coding: utf-8 -*-
"""ROM 메타데이터/시리얼/BIOS 참조 DB 조회."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..connection import open_readonly

PathLike = Union[str, Path]
logger = logging.getLogger(__name__)


class MetadataRepository:
    def __init__(self, db_path: PathLike):
        self.db_path = Path(db_path)
        self._cache: Dict[str, Any] = {}

    def is_available(self) -> bool:
        return self.db_path.is_file()

    def find_arcade_romset(self, rom_name: str) -> Optional[Dict[str, Any]]:
        if not rom_name:
            return None
        key = rom_name.lower().strip()
        cache_key = f"arc:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        connection = open_readonly(self.db_path)
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM arcade_romsets WHERE rom_name = ?", (key,)
            ).fetchone()
            if not row:
                return None
            result = {
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
                "cores": json.loads(row["recommended_cores"] or '["fbneo", "mame"]'),
            }
            self._cache[cache_key] = result
            return result
        except Exception as exc:
            logger.debug("arcade romset query failed for %s: %s", key, exc)
            return None
        finally:
            connection.close()

    def find_disc_serial(self, serial: str) -> Optional[Dict[str, Any]]:
        if not serial:
            return None
        key = serial.strip().upper()
        cache_key = f"ser:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        connection = open_readonly(self.db_path)
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM disc_serials WHERE serial = ?", (key,)
            ).fetchone()
            if not row:
                key_clean = key.replace("-", "").replace("_", "")
                row = connection.execute(
                    "SELECT * FROM disc_serials WHERE REPLACE(REPLACE(serial, '-', ''), '_', '') = ?",
                    (key_clean,),
                ).fetchone()
            if not row:
                return None
            result = {
                "serial": row["serial"],
                "system_id": row["system_id"],
                "title": row["title"],
                "region": row["region"],
                "manufacturer": row["manufacturer"],
            }
            self._cache[cache_key] = result
            return result
        except Exception as exc:
            logger.debug("disc serial query failed for %s: %s", key, exc)
            return None
        finally:
            connection.close()

    def find_bios_manifest(self, system_id: str) -> Optional[Dict[str, Any]]:
        if not system_id:
            return None
        key = system_id.lower().strip()
        cache_key = f"bio:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        connection = open_readonly(self.db_path)
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM bios_manifest WHERE system_id = ?", (key,)
            ).fetchone()
            if not row:
                return None
            result = {
                "system_id": row["system_id"],
                "system_name": row["system_name"],
                "mandatory": bool(row["mandatory"]),
                "bios_files": json.loads(row["bios_files"] or "[]"),
                "description": row["description"],
            }
            self._cache[cache_key] = result
            return result
        except Exception as exc:
            logger.debug("bios manifest query failed for %s: %s", key, exc)
            return None
        finally:
            connection.close()

    def clear_cache(self) -> None:
        self._cache.clear()
