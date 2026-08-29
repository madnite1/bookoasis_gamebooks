# -*- coding: utf-8 -*-
"""
세가(Sega) 콘솔 및 핸드헬드 카트리지 ROM 바이너리 헤더 분석기.
Master System, Game Gear, Mega Drive / Genesis, 32X 헤더 분석.
"""

import os
import array
import sys
import unicodedata
from typing import Optional

from ..models import RomAnalysisResult, HeaderMetadata, DetectionEvidence


def _clean_str(val: bytes) -> str:
    """바이너리 문자열에서 널 바이트 및 공백 깔끔하게 제거 (Shift-JIS 및 NFKC 정규화 지원)"""
    if not val:
        return ""
    try:
        text = val.decode("shift_jis")
        text = unicodedata.normalize("NFKC", text).replace("\x00", "").strip()
        if text and sum(1 for c in text if c.isprintable()) / len(text) > 0.7:
            return text
    except UnicodeError:
        pass

    text = val.decode("latin-1", errors="ignore").replace("\x00", "").strip()
    return text


def _evidence_kwargs(method: str, confidence: float, detail: str):
    methods = [method]
    if method != "extension_hint":
        methods.append("binary_header")
    return {
        "confidence_score": confidence,
        "evidence": [DetectionEvidence(method=method, confidence=confidence, detail=detail, source="sega_header")],
        "detection_methods": methods,
    }


class SegaHeaderDetector:
    """세가 카트리지 기종 ROM 헤더 분석"""

    @classmethod
    def detect(cls, file_path: str, data: bytes, signature_only: bool = False, total_size: Optional[int] = None) -> Optional[RomAnalysisResult]:
        ext = os.path.splitext(file_path)[1].lower()
        size = len(data)
        base_name = os.path.basename(file_path)

        # 강한 시그니처를 확장자보다 우선한다.
        if size >= 0x200 and b"SEGA" in data[0x100:0x200]:
            return cls._detect_megadrive(file_path, base_name, data, ext, total_size=total_size)

        if any(size >= offset + 16 and data[offset:offset+8] == b"TMR SEGA" for offset in [0x7FF0, 0x3FF0, 0x1FF0]):
            return cls._detect_sms_gg(file_path, base_name, data, ext)

        if signature_only:
            return None

        if ext in [".sms", ".gg", ".sg"]:
            return cls._detect_sms_gg(file_path, base_name, data, ext)

        if ext in [".md", ".smd", ".gen", ".32x"]:
            return cls._detect_megadrive(file_path, base_name, data, ext, total_size=total_size)

        # .bin은 강한 SEGA 헤더가 있을 때만 카트리지로 취급한다.
        return None

    @classmethod
    def _detect_sms_gg(cls, file_path: str, base_name: str, data: bytes, ext: str) -> RomAnalysisResult:
        """Sega Master System / Game Gear 헤더 분석"""
        size = len(data)
        is_gg = ext == ".gg"
        title = os.path.splitext(base_name)[0]

        # TMR SEGA 헤더 탐색 (0x7FF0, 0x3FF0, 0x1FF0)
        tmr_found = False
        tmr_offset = None
        for offset in [0x7FF0, 0x3FF0, 0x1FF0]:
            if size >= offset + 16 and data[offset:offset+8] == b"TMR SEGA":
                tmr_found = True
                tmr_offset = offset
                region_code = (data[offset+0x0F] >> 4) & 0x0F
                if region_code in [5, 6, 7]:
                    is_gg = True
                break

        system_id = "gamegear" if is_gg else "mastersystem"
        system_name = "Sega Game Gear" if is_gg else "Sega Master System"
        libretro_sys = "Sega_-_Game_Gear" if is_gg else "Sega_-_Master_System_-_Mark_III"
        if tmr_found:
            evidence_method, evidence_conf = "sms_gg_tmr_header", 0.98
            evidence_detail = f"TMR SEGA header validated at 0x{tmr_offset:X}"
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension; TMR SEGA header not found"

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=size,
            file_ext=ext,
            system_id=system_id,
            system_name=system_name,
            system_type="handheld" if is_gg else "console",
            platform_slug=system_id,
            libretro_system=libretro_sys,
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            header_metadata=HeaderMetadata(title=title, header_type="SMS/GG TMR SEGA Header"),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"{system_name} 카트리지 롬"
        )

    @classmethod
    def _detect_megadrive(cls, file_path: str, base_name: str, data: bytes, ext: str, total_size: Optional[int] = None) -> RomAnalysisResult:
        """Mega Drive / Genesis / 32X 헤더 분석"""
        size = len(data)
        is_32x = ext == ".32x"
        title = None
        serial = None
        region = None

        sega_signature = size >= 0x200 and b"SEGA" in data[0x100:0x200]
        checksum_stored = int.from_bytes(data[0x18E:0x190], "big") if size >= 0x190 else None
        checksum_calc = None
        checksum_valid = False
        complete_rom_loaded = total_size is None or total_size <= size
        if size > 0x200 and complete_rom_loaded:
            body = data[0x200:]
            even_size = len(body) & ~1
            words = array.array("H")
            words.frombytes(body[:even_size])
            if sys.byteorder == "little":
                words.byteswap()
            checksum_calc = sum(words)
            if even_size < len(body):
                checksum_calc += body[-1] << 8
            checksum_calc &= 0xFFFF
            checksum_valid = checksum_stored is not None and checksum_stored == checksum_calc

        if size >= 0x200:
            console_name = _clean_str(data[0x100:0x110])
            if "32X" in console_name:
                is_32x = True

            dom_title = _clean_str(data[0x120:0x150])
            intl_title = _clean_str(data[0x150:0x180])
            title = intl_title or dom_title or os.path.splitext(base_name)[0]

            serial = _clean_str(data[0x180:0x18E])
            region_str = _clean_str(data[0x1F0:0x200])
            if region_str:
                region = f"Region: {region_str}"

        system_id = "sega32x" if is_32x else "megadrive"
        system_name = "Sega 32X" if is_32x else "Sega Mega Drive / Genesis"
        libretro_sys = "Sega_-_32X" if is_32x else "Sega_-_Mega_Drive_-_Genesis"
        if sega_signature and checksum_valid:
            evidence_method, evidence_conf = "megadrive_checksum_header", 0.995
            evidence_detail = f"SEGA internal cartridge header and checksum validated at 0x100 (0x{checksum_stored:04X})"
        elif sega_signature:
            evidence_method, evidence_conf = "megadrive_sega_header", 0.96
            evidence_detail = (
                "SEGA internal cartridge header validated at 0x100; full-ROM checksum was unavailable"
                if not complete_rom_loaded
                else "SEGA internal cartridge header validated at 0x100; checksum did not validate"
            )
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension; SEGA internal header not validated"

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=size,
            file_ext=ext,
            system_id=system_id,
            system_name=system_name,
            system_type="console",
            platform_slug=system_id,
            libretro_system=libretro_sys,
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            header_metadata=HeaderMetadata(
                title=title or os.path.splitext(base_name)[0],
                serial=serial or None,
                region=region,
                header_type="Sega Genesis Internal Header",
                internal_checksum=(f"{checksum_stored:04x}" if checksum_stored is not None else None)
            ),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"{system_name} 카트리지 롬 (타이틀: {title or 'N/A'})"
        )
