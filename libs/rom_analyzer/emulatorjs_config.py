# -*- coding: utf-8 -*-
"""EmulatorJS Stable 실행 환경의 공통 설정.

코어 카탈로그와 분석 결과가 같은 Stable 버전/BIOS 정의를 사용하도록
변경 가능성이 있는 실행 환경 메타데이터를 한 곳에 모은다.
"""

from typing import Any, Dict


EMULATORJS_STABLE_VERSION = "4.2.3"


# EmulatorJS 지원 기종에 대해 rom-analyzer가 노출하는 BIOS 요구사항의 단일 소스.
# files는 모두 동시에 필요한 목록이 아니라 해당 시스템/지역에서 사용할 수 있는
# 공식 파일명 후보를 뜻한다. mandatory=False는 BIOS 없이도 코어가 동작할 수 있음을 뜻한다.
EMULATORJS_BIOS_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "psx": {
        "system_name": "Sony PlayStation (PS1)",
        "mandatory": True,
        "bios_files": ["scph5500.bin", "scph5501.bin", "scph5502.bin"],
        "description": "EmulatorJS PlayStation BIOS (JP: scph5500, US: scph5501, EU: scph5502)",
    },
    "saturn": {
        "system_name": "Sega Saturn",
        "mandatory": True,
        "bios_files": ["saturn_bios.bin"],
        "description": "EmulatorJS Yabause용 Saturn BIOS (saturn_bios.bin)",
    },
    "segacd": {
        "system_name": "Sega CD / Mega CD",
        "mandatory": True,
        "bios_files": ["bios_CD_E.bin", "bios_CD_U.bin", "bios_CD_J.bin"],
        "description": "EmulatorJS Sega CD / Mega CD 지역별 BIOS",
    },
    "pcecd": {
        "system_name": "NEC PC Engine CD-ROM² / TurboGrafx-CD",
        "mandatory": True,
        "bios_files": ["syscard3.pce"],
        "description": "PC Engine CD-ROM² System Card 3.0 BIOS",
    },
    "3do": {
        "system_name": "Panasonic 3DO Interactive Multiplayer",
        "mandatory": True,
        "bios_files": ["panafz10.bin", "panafz1.bin"],
        "description": "Opera 코어에서 사용하는 3DO BIOS 후보",
    },
    "fds": {
        "system_name": "Nintendo Family Computer Disk System",
        "mandatory": True,
        "bios_files": ["disksys.rom"],
        "description": "Famicom Disk System BIOS (disksys.rom)",
    },
    "gba": {
        "system_name": "Nintendo Game Boy Advance",
        "mandatory": False,
        "bios_files": ["gba_bios.bin"],
        "description": "GBA 공식 BIOS (선택 사항)",
    },
    "nds": {
        "system_name": "Nintendo DS",
        "mandatory": False,
        "bios_files": ["bios7.bin", "bios9.bin", "firmware.bin"],
        "description": "Nintendo DS BIOS 및 firmware 파일 (코어 설정에 따라 선택 사항)",
    },
    "lynx": {
        "system_name": "Atari Lynx",
        "mandatory": False,
        "bios_files": ["lynxboot.img"],
        "description": "Atari Lynx Boot ROM (선택 사항)",
    },
}
