# -*- coding: utf-8 -*-
"""MAME2003 계열 게임별 실행 호환성 해석 래퍼.

원시 SQLite 조회는 rom_database가 담당하고, 이 모듈은 기존 Analyzer 공개 모델로
변환하여 하위 호환 API를 유지한다.
"""

from functools import lru_cache
from typing import Dict, Optional

from rom_database.paths import DatabasePaths
from rom_database.repositories.compatibility import CompatibilityRepository, CORE_SOURCE_URLS

from ..database_context import get_active_database
from ..models import ArcadeCoreCompatibility


_DEFAULT_COMPAT_DB_PATH = str(DatabasePaths.default().compatibility)
_COMPAT_DB_PATH = _DEFAULT_COMPAT_DB_PATH
_WORKING_DRIVER_STATUS = "good"


class ArcadeCompatibilityManager:
    """MAME2003 호환성 raw record를 Analyzer 결과 모델로 변환한다."""

    @classmethod
    def _repository(cls) -> CompatibilityRepository:
        # 과거 _COMPAT_DB_PATH 교체 계약은 유지하되, 기본 경로일 때는 현재 분석 컨텍스트의 DB를 쓴다.
        if _COMPAT_DB_PATH != _DEFAULT_COMPAT_DB_PATH:
            return CompatibilityRepository(_COMPAT_DB_PATH)
        return get_active_database().compatibility

    @classmethod
    def is_available(cls) -> bool:
        return cls._repository().is_available()

    @classmethod
    def get(cls, rom_name: str, core_id: str) -> Optional[ArcadeCoreCompatibility]:
        repository = cls._repository()
        database_key = str(repository.db_path.resolve())
        return cls._get_cached(database_key, rom_name, core_id)

    @classmethod
    @lru_cache(maxsize=4096)
    def _get_cached(
        cls, database_key: str, rom_name: str, core_id: str
    ) -> Optional[ArcadeCoreCompatibility]:
        record = CompatibilityRepository(database_key).find(rom_name, core_id)
        if record is None:
            return None
        return ArcadeCoreCompatibility(
            core_id=record.core_id,
            rom_name=record.rom_name,
            description=record.description,
            supported=record.driver_status == _WORKING_DRIVER_STATUS,
            driver_status=record.driver_status,
            color_status=record.color_status,
            sound_status=record.sound_status,
            graphics_status=record.graphics_status,
            samples=record.samples,
            bios_required=record.bios_required,
            source_url=record.source_url,
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
        cls._get_cached.cache_clear()
