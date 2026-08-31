# -*- coding: utf-8 -*-
"""아케이드 참조 카탈로그 호환 API와 콘솔 런타임 BIOS 정책."""

from typing import Any, Dict

from rom_database.catalogs.bios import ARCADE_BIOS_SETS, ARCADE_DEVICE_SETS
from ..emulatorjs_config import EMULATORJS_BIOS_REQUIREMENTS

# EmulatorJS Stable 범위 밖에서 기존 분석 호환을 위해 유지하는 BIOS 요구사항.
# EmulatorJS 지원 기종은 emulatorjs_config.EMULATORJS_BIOS_REQUIREMENTS에서 단일 관리한다.
CONSOLE_BIOS_CATALOG: Dict[str, Dict[str, Any]] = {
    "ps2": {
        "system_name": "Sony PlayStation 2",
        "mandatory": True,
        "bios_files": ["scph39001.bin", "scph70012.bin", "SCPH-70012_BIOS_V12_USA_200.bin"],
        "description": "PlayStation 2 구동을 위한 BIOS 롬 파일",
    },
    "dreamcast": {
        "system_name": "Sega Dreamcast",
        "mandatory": True,
        "bios_files": ["dc_boot.bin", "dc_flash.bin"],
        "description": "드림캐스트 부트 바이오스 (dc_boot.bin 및 플래시 dc_flash.bin)",
    },
    "pcfx": {
        "system_name": "NEC PC-FX",
        "mandatory": True,
        "bios_files": ["pcfx.rom"],
        "description": "NEC PC-FX 시스템 바이오스",
    },
    "neocd": {
        "system_name": "SNK Neo Geo CD",
        "mandatory": True,
        "bios_files": ["neocd.bin", "neocd_f.rom", "neocd_z.rom"],
        "description": "네오지오 CD 시스템 바이오스",
    },
}

# EmulatorJS 지원 범위는 공통 설정을 그대로 노출하여 분석기/코어 카탈로그 간 불일치를 막는다.
for _system_id, _requirement in EMULATORJS_BIOS_REQUIREMENTS.items():
    CONSOLE_BIOS_CATALOG[_system_id] = dict(_requirement)

__all__ = ["ARCADE_BIOS_SETS", "ARCADE_DEVICE_SETS", "CONSOLE_BIOS_CATALOG"]
