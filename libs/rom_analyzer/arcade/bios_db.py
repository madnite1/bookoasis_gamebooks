# -*- coding: utf-8 -*-
"""
아케이드 및 시스템 바이오스 / 장치 롬셋 데이터베이스.
MAME / FBNeo 바이오스 롬셋 및 필요한 장치 롬 정의.
"""

from typing import Dict, Any

from ..emulatorjs_config import EMULATORJS_BIOS_REQUIREMENTS

ARCADE_BIOS_SETS: Dict[str, Dict[str, Any]] = {
    "neogeo": {
        "name": "SNK Neo-Geo MVS / AES BIOS",
        "board": "SNK Neo-Geo MVS",
        "system_id": "neogeo",
        "platform_slug": "neogeo",
        "libretro_system": "SNK_-_Neo_Geo",
        "files": ["asia-s3.rom", "sm1.sm1", "sfix.sfix", "000-lo.lo", "sp-s2.sp1", "neo-epo.bin", "uni-bios.rom"],
        "description": "네오지오 MVS/AES 시스템 필수 바이오스",
    },
    "pgm": {
        "name": "IGS PolyGame Master (PGM) System BIOS",
        "board": "IGS PolyGame Master (PGM)",
        "system_id": "pgm",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["pgm_m01s.rom", "pgm_p01s.rom", "pgm_t01s.rom"],
        "description": "IGS PGM(삼국전기, 데몬프론트 등) 필수 바이오스",
    },
    "naomi": {
        "name": "Sega NAOMI BIOS",
        "board": "Sega NAOMI",
        "system_id": "naomi",
        "platform_slug": "naomi",
        "libretro_system": "Sega_-_NAOMI",
        "files": ["naomi_boot.bin", "epr-21576d.ic27", "epr-21577.ic27", "epr-21578.ic27", "epr-21579.ic27"],
        "description": "세가 나오미 아케이드 기판 필수 바이오스",
    },
    "naomi2": {
        "name": "Sega NAOMI 2 BIOS",
        "board": "Sega NAOMI 2",
        "system_id": "naomi2",
        "platform_slug": "naomi",
        "libretro_system": "Sega_-_NAOMI",
        "files": ["epr-23605.ic27", "epr-23606.ic27", "epr-23607.ic27", "epr-23608.ic27"],
        "description": "세가 나오미 2 아케이드 기판 필수 바이오스",
    },
    "naomigd": {
        "name": "Sega NAOMI GD-ROM DIMM Board Firmware",
        "board": "Sega NAOMI GD-ROM",
        "system_id": "naomi",
        "platform_slug": "naomi",
        "libretro_system": "Sega_-_NAOMI",
        "files": ["epr-21576h.ic27", "dimm_firmware.bin"],
        "description": "세가 나오미 GD-ROM 구동용 펌웨어",
    },
    "stv": {
        "name": "Sega Titan Video (ST-V) BIOS",
        "board": "Sega ST-V",
        "system_id": "stv",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["epr-17951a.ic8", "epr-17952a.ic8", "epr-17954a.ic8"],
        "description": "세가 ST-V (새턴 기반 아케이드) 필수 바이오스",
    },
    "awbios": {
        "name": "Sammy Atomiswave BIOS",
        "board": "Sammy Atomiswave",
        "system_id": "atomiswave",
        "platform_slug": "atomiswave",
        "libretro_system": "Sammy_-_Atomiswave",
        "files": ["bios.ic23", "bios.ic23_pal", "bios.ic23_japan"],
        "description": "새미 아토미스웨이브(KOF XI, 메탈슬러그 6 등) 필수 바이오스",
    },
    "skns": {
        "name": "Kaneko Super Nova System BIOS",
        "board": "Kaneko Super Nova",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["skns_bios.bin", "skns.bin"],
        "description": "카네코 슈퍼노바 기판 (갈스패닉 등) 필수 바이오스",
    },
    "decocass": {
        "name": "DECO Cassette System BIOS",
        "board": "Data East DECO Cassette",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["cassbios.bin"],
        "description": "데이터 이스트 카세트 시스템 바이오스",
    },
    "konamigx": {
        "name": "Konami GX System BIOS",
        "board": "Konami GX",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["gx_bios.bin"],
        "description": "코나미 GX 시스템 기판 바이오스",
    },
    "sys246": {
        "name": "Namco System 246 BIOS",
        "board": "Namco System 246",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["sys246_bios.bin"],
        "description": "남코 시스템 246 (PS2 기반 아케이드) 바이오스",
    },
    "sys573": {
        "name": "Konami System 573 BIOS",
        "board": "Konami System 573",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["700a01.bin"],
        "description": "코나미 시스템 573 (DDR, 기타프릭스 등) 바이오스",
    },
    "hikaru": {
        "name": "Sega Hikaru BIOS",
        "board": "Sega Hikaru",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["epr-23164.ic2", "epr-23165.ic3"],
        "description": "세가 히카루 기판 바이오스",
    },
    "playch10": {
        "name": "Nintendo PlayChoice-10 BIOS",
        "board": "Nintendo PlayChoice-10",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["pch1-c.8k", "pch1-b.8k"],
        "description": "닌텐도 플레이초이스-10 바이오스",
    },
    "nss": {
        "name": "Nintendo Super System BIOS",
        "board": "Nintendo Super System",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["nss-c.dat", "nss-ic1.dat"],
        "description": "닌텐도 슈퍼 시스템 아케이드 바이오스",
    },
    "megatech": {
        "name": "Sega Mega-Tech BIOS",
        "board": "Sega Mega-Tech",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["mt_bios.bin"],
        "description": "세가 메가텍 아케이드 바이오스",
    },
    "megaplay": {
        "name": "Sega Mega-Play BIOS",
        "board": "Sega Mega-Play",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["mp_bios.bin"],
        "description": "세가 메가플레이 아케이드 바이오스",
    },
    "cpzn1": {
        "name": "Capcom Sony ZN-1 BIOS",
        "board": "Capcom / Sony ZN-1",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["zn1_bios.bin"],
        "description": "캡콤 ZN-1 3D 아케이드 기판 바이오스",
    },
    "cpzn2": {
        "name": "Capcom Sony ZN-2 BIOS",
        "board": "Capcom / Sony ZN-2",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["zn2_bios.bin"],
        "description": "캡콤 ZN-2 3D 아케이드 기판 바이오스",
    },
    "gnet": {
        "name": "Taito G-Net BIOS",
        "board": "Taito G-Net",
        "system_id": "arcade",
        "platform_slug": "arcade",
        "libretro_system": "MAME",
        "files": ["gnet_bios.bin", "flash.bin"],
        "description": "타이토 G-NET 시스템 바이오스",
    }
}

# 아케이드 장치 / 오디오 칩 롬셋 목록 (독립 실행 불가, 부모/장치 롬셋)
ARCADE_DEVICE_SETS: Dict[str, Dict[str, Any]] = {
    "qsound": {
        "name": "Capcom QSound Audio DSP ROM",
        "board": "Capcom CPS-1.5 / CPS-2",
        "description": "캡콤 QSound 음원 칩 DSP 롬 (sl-163.bin, qsound.bin)",
    },
    "qsound_hle": {
        "name": "Capcom QSound HLE Audio DSP ROM",
        "board": "Capcom CPS-2",
        "description": "캡콤 QSound HLE DSP 롬",
    },
    "ym2608": {
        "name": "Yamaha YM2608 Sound Chip Delta-T ROM",
        "board": "Yamaha Sound Device",
        "description": "야마하 OPNA 사운드 칩 ROM",
    },
    "ym2610": {
        "name": "Yamaha YM2610 Sound Chip ADPCM ROM",
        "board": "SNK Neo-Geo / Yamaha Sound Device",
        "description": "야마하 OPNB 사운드 칩 ROM (000-lo.lo)",
    },
    "ymf278b": {
        "name": "Yamaha YMF278B (OPL4) Sound Chip ROM",
        "board": "Yamaha Sound Device",
        "description": "야마하 OPL4 사운드 ROM (yrw801.rom)",
    },
    "namcoc70": {
        "name": "Namco C70 MCU Device",
        "board": "Namco System 1",
        "description": "남코 C70 사운드/키 MCU 롬",
    },
    "namcoc75": {
        "name": "Namco C75 MCU Device",
        "board": "Namco System 2",
        "description": "남코 C75 사운드/키 MCU 롬",
    },
    "midssio": {
        "name": "Midway Sound I/O Controller",
        "board": "Midway Wolfunit / Seattle",
        "description": "미드웨이 사운드 보드 I/O 롬",
    },
    "dcs2_audio": {
        "name": "Midway DCS2 Sound System",
        "board": "Midway Seattle / Zeus",
        "description": "미드웨이 DCS2 오디오 롬",
    }
}


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
