# -*- coding: utf-8 -*-
"""
디스크 미디어(CD/DVD/GD-ROM) 내부 시리얼, 시스템 헤더 및 타이틀 추출기.
PS1, PS2, Saturn, Dreamcast, Sega CD, PSP, 3DO, GameCube 등 판별.
"""

import re
import struct
from typing import Optional, Dict, Any, Tuple

# 정규식 패턴들 (PS1 vs PS2 구분)
PS1_BOOT_RE = re.compile(rb"BOOT\s*=\s*cdrom:?\\?([A-Za-z]{4})[-_]?([0-9]{3})\.?([0-9]{2})", re.IGNORECASE)
PS2_BOOT_RE = re.compile(rb"BOOT2\s*=\s*cdrom0?:?\\?([A-Za-z]{4})[-_]?([0-9]{3})\.?([0-9]{2})", re.IGNORECASE)
PSP_DISC_ID_RE = re.compile(rb"([A-Z]{4})_?([0-9]{5})")
SEGA_SATURN_SIG = b"SEGA SEGASATURN"
SEGA_DREAMCAST_SIG = b"SEGA SEGAKATANA"
SEGA_CD_SIG = b"SEGA MEGA_CD"
SEGA_GENESIS_CD_SIG = b"SEGA SEGA_CD"
GAMECUBE_MAGIC = 0x5D1C9EA3


def _clean_str(val: bytes) -> str:
    """바이트 문자열에서 널 바이트 및 공백 깔끔하게 제거"""
    if not val:
        return ""
    text = val.decode("latin-1", errors="ignore")
    return text.replace("\x00", "").strip()


class DiscSerialScanner:
    """디스크 미디어 바이너리 헤더 정밀 분석기"""

    @classmethod
    def scan_binary_chunk(cls, data: bytes) -> Dict[str, Any]:
        """
        바이트 블록(최대 수 MB 또는 헤더 청크)에서 콘솔 시스템 및 시리얼 정보 추출
        """
        result = {
            "system_id": None,
            "system_name": None,
            "title": None,
            "serial": None,
            "region": None,
            "header_type": None
        }

        if not data:
            return result

        # 1. Sega Saturn IP.BIN (보통 0x00 또는 0x10에서 시작)
        if SEGA_SATURN_SIG in data[:0x800]:
            idx = data.find(SEGA_SATURN_SIG)
            ip_header = data[idx:idx + 0x100]
            hardware_id = _clean_str(ip_header[:16])
            maker_id = _clean_str(ip_header[0x10:0x20])
            product_num = _clean_str(ip_header[0x20:0x30])
            version = _clean_str(ip_header[0x30:0x36])
            release_date = _clean_str(ip_header[0x36:0x40])
            region_str = _clean_str(ip_header[0x40:0x50])
            title = _clean_str(ip_header[0x60:0xE0])

            result["system_id"] = "saturn"
            result["system_name"] = "Sega Saturn"
            result["title"] = title or None
            result["serial"] = product_num or None
            result["region"] = region_str or None
            result["header_type"] = "Saturn IP.BIN"
            return result

        # 2. Sega Dreamcast IP.BIN
        if SEGA_DREAMCAST_SIG in data[:0x800] or (b"SEGA ENTERPRISES" in data[:0x100] and b"GD-ROM" in data[:0x200]):
            idx = data.find(SEGA_DREAMCAST_SIG) if SEGA_DREAMCAST_SIG in data else data.find(b"SEGA ENTERPRISES")
            ip_header = data[idx:idx + 0x100]
            hardware_id = _clean_str(ip_header[:16])
            maker_id = _clean_str(ip_header[0x10:0x20])
            product_num = _clean_str(ip_header[0x20:0x30])
            region_str = _clean_str(ip_header[0x30:0x38])
            title = _clean_str(ip_header[0x80:0x100])

            result["system_id"] = "dreamcast"
            result["system_name"] = "Sega Dreamcast"
            result["title"] = title or None
            result["serial"] = product_num or None
            result["region"] = region_str or None
            result["header_type"] = "Dreamcast IP.BIN"
            return result

        # 3. Sega CD / Mega-CD
        if SEGA_CD_SIG in data[:0x800] or SEGA_GENESIS_CD_SIG in data[:0x800]:
            idx = data.find(b"SEGA")
            hdr = data[idx:idx + 0x200]
            dom_title = _clean_str(hdr[0x20:0x50])
            intl_title = _clean_str(hdr[0x50:0x80])
            serial = _clean_str(hdr[0x80:0x8E])

            result["system_id"] = "segacd"
            result["system_name"] = "Sega CD / Mega-CD"
            result["title"] = intl_title or dom_title or None
            result["serial"] = serial or None
            result["header_type"] = "Mega-CD Header"
            return result

        # 4. PlayStation 2 SYSTEM.CNF (BOOT2)
        ps2_match = PS2_BOOT_RE.search(data)
        if ps2_match:
            prefix = ps2_match.group(1).decode("ascii", errors="ignore").upper()
            num1 = ps2_match.group(2).decode("ascii", errors="ignore")
            num2 = ps2_match.group(3).decode("ascii", errors="ignore")
            serial = f"{prefix}-{num1}{num2}"

            result["system_id"] = "ps2"
            result["system_name"] = "Sony PlayStation 2"
            result["serial"] = serial
            result["header_type"] = "PS2 SYSTEM.CNF"
            result["region"] = cls._region_from_ps_serial(serial)
            return result

        # 5. PlayStation 1 SYSTEM.CNF (BOOT)
        ps1_match = PS1_BOOT_RE.search(data)
        if ps1_match:
            prefix = ps1_match.group(1).decode("ascii", errors="ignore").upper()
            num1 = ps1_match.group(2).decode("ascii", errors="ignore")
            num2 = ps1_match.group(3).decode("ascii", errors="ignore")
            serial = f"{prefix}-{num1}{num2}"

            # PS2 전용 시리얼 접두사 체크 (SCES/SLES/SLUS/SLPM/SLPS 5xxxx 이상은 PS2)
            is_ps2_serial = (prefix in ["SCES", "SLES", "SLUS", "SLPM", "SLPS", "SCKA", "SCAJ"] and num1.startswith("5"))
            if is_ps2_serial:
                result["system_id"] = "ps2"
                result["system_name"] = "Sony PlayStation 2"
            else:
                result["system_id"] = "psx"
                result["system_name"] = "Sony PlayStation"

            result["serial"] = serial
            result["header_type"] = "PS1 SYSTEM.CNF"
            result["region"] = cls._region_from_ps_serial(serial)
            return result

        # 6. PS-X EXE Header
        if b"PS-X EXE\x00\x00\x00\x00\x00\x00\x00\x00" in data[:0x800]:
            result["system_id"] = "psx"
            result["system_name"] = "Sony PlayStation"
            result["header_type"] = "PS-X EXE"
            return result

        # 7. Nintendo GameCube / Wii
        if len(data) >= 0x40:
            magic = struct.unpack(">I", data[0x1C:0x20])[0] if len(data) >= 0x20 else 0
            if magic == GAMECUBE_MAGIC:
                game_code = data[:4].decode("latin-1", errors="ignore").strip()
                maker_code = data[4:6].decode("latin-1", errors="ignore").strip()
                title = data[0x20:0x60].decode("latin-1", errors="ignore").strip()
                is_wii = game_code.startswith("R") or game_code.startswith("S")
                result["system_id"] = "wii" if is_wii else "gamecube"
                result["system_name"] = "Nintendo Wii" if is_wii else "Nintendo GameCube"
                result["title"] = title or None
                result["serial"] = f"{game_code}-{maker_code}"
                result["header_type"] = "GameCube/Wii Header"
                return result

        # 8. 3DO Volume Descriptor
        if b"\x01\x5a\x5a\x5a\x5a\x5a\x01" in data[:0x800]:
            result["system_id"] = "3do"
            result["system_name"] = "Panasonic 3DO Interactive Multiplayer"
            result["header_type"] = "3DO Volume Descriptor"
            return result

        # 9. PC Engine CD-ROM
        if b"PC-Engine CD-ROM SYSTEM" in data[:0x800] or b"SUPER CD-ROM2" in data[:0x800]:
            result["system_id"] = "pcecd"
            result["system_name"] = "NEC PC Engine CD-ROM²"
            result["header_type"] = "PCE-CD Header"
            return result

        return result

    @classmethod
    def _region_from_ps_serial(cls, serial: str) -> Optional[str]:
        """PlayStation 시리얼 접두사로 국가/지역 식별"""
        s = serial.upper()
        if s.startswith("SLUS") or s.startswith("SCUS"):
            return "NTSC-U (North America)"
        if s.startswith("SLES") or s.startswith("SCES"):
            return "PAL (Europe)"
        if s.startswith("SLPS") or s.startswith("SLPM") or s.startswith("SCPS"):
            return "NTSC-J (Japan)"
        if s.startswith("SLKA") or s.startswith("SCKA"):
            return "NTSC-K (Korea)"
        if s.startswith("SLAJ") or s.startswith("SCAJ"):
            return "NTSC-Asia"
        return None
