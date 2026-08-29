# -*- coding: utf-8 -*-
"""
닌텐도(Nintendo) 콘솔 및 핸드헬드 ROM 바이너리 헤더 분석기.
NES, FDS, SNES, GB, GBC, GBA, NDS, N64 헤더 파싱 및 특수 칩 탐지.
"""

import os
import struct
import unicodedata
from typing import Optional, Dict, Any

from ..models import RomAnalysisResult, HeaderMetadata, BiosInfo, DetectionEvidence
from ..db import query_disc_serial


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

GB_NINTENDO_LOGO = bytes.fromhex(
    "CE ED 66 66 CC 0D 00 0B 03 73 00 83 00 0C 00 0D "
    "00 08 11 1F 88 89 00 0E DC CC 6E E6 DD DD D9 99 "
    "BB BB 67 63 6E 0E EC CC DD DC 99 9F BB B9 33 3E"
)

GBA_NINTENDO_LOGO = bytes.fromhex(
    "24ffae51699aa2213d84820a84e409ad11248b98c0817f21a352be199309ce20"
    "10464a4af82731ec58c7e83382e3cebf85f4df94ce4b09c194568ac01372a7fc"
    "9f844d73a3ca9a615897a327fc039876231dc7610304ae56bf38840040a70efd"
    "ff52fe036f9530f197fbc08560d68025a963be03014e38e2f9a234ffbb3e0344"
    "780090cb88113a9465c07c6387f03cafd625e48b380aac7221d4f807"
)


def _gb_header_checksum(data: bytes) -> Optional[int]:
    if len(data) <= 0x14D:
        return None
    value = 0
    for byte in data[0x134:0x14D]:
        value = (value - byte - 1) & 0xFF
    return value


def _gba_header_checksum(data: bytes) -> Optional[int]:
    if len(data) <= 0xBD:
        return None
    return (-sum(data[0xA0:0xBD]) - 0x19) & 0xFF


def _nintendo_crc16(data: bytes, initial: int = 0xFFFF) -> int:
    """Nintendo DS 계열 헤더가 사용하는 CRC16 (poly 0xA001)."""
    crc = initial & 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if (crc & 1) else 0)
    return crc & 0xFFFF



def _evidence_kwargs(method: str, confidence: float, detail: str, generic: bool = True):
    methods = [method]
    if generic and method != "extension_hint":
        methods.append("binary_header")
    return {
        "confidence_score": confidence,
        "evidence": [DetectionEvidence(method=method, confidence=confidence, detail=detail, source="nintendo_header")],
        "detection_methods": methods,
    }



class NintendoHeaderDetector:
    """닌텐도 기종 ROM 헤더 분석"""

    @classmethod
    def detect(cls, file_path: str, data: bytes, total_size: Optional[int] = None, signature_only: bool = False) -> Optional[RomAnalysisResult]:
        ext = os.path.splitext(file_path)[1].lower()
        size = total_size or len(data)
        base_name = os.path.basename(file_path)

        # 강한 바이너리 시그니처는 확장자보다 먼저 판별한다.
        if data.startswith(b"NES\x1a") or data.startswith(b"FDS\x1a") or b"*NINTENDO-HVC*" in data[:0x100]:
            return cls._detect_nes_fds(file_path, base_name, data, ext, total_size=size)

        if size >= 4 and data[:4] in [b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"]:
            return cls._detect_n64(file_path, base_name, data, ext)

        if len(data) >= 0x150 and data[0x104:0x134] == GB_NINTENDO_LOGO:
            return cls._detect_gb(file_path, base_name, data, ext)

        if len(data) >= 0xC0 and data[0x04:0xA0] == GBA_NINTENDO_LOGO and data[0xB2] == 0x96:
            return cls._detect_gba(file_path, base_name, data, ext)

        if len(data) >= 0x160 and data[0xC0:0x15C] == GBA_NINTENDO_LOGO:
            logo_crc = int.from_bytes(data[0x15C:0x15E], "little")
            header_crc = int.from_bytes(data[0x15E:0x160], "little")
            if logo_crc == _nintendo_crc16(data[0xC0:0x15C]) and header_crc == _nintendo_crc16(data[:0x15E]):
                return cls._detect_nds(file_path, base_name, data, ext, total_size=size)

        if signature_only:
            return None

        # 강한 시그니처가 없을 때만 확장자를 힌트로 사용한다.
        if ext in [".nes", ".fds", ".unf"]:
            return cls._detect_nes_fds(file_path, base_name, data, ext, total_size=size)
        if ext in [".sfc", ".smc", ".swc", ".fig"]:
            return cls._detect_snes(file_path, base_name, data, ext, total_size=size)
        if ext in [".gb", ".gbc"]:
            return cls._detect_gb(file_path, base_name, data, ext)
        if ext in [".gba", ".agb"]:
            return cls._detect_gba(file_path, base_name, data, ext)
        if ext in [".nds", ".ds"]:
            return cls._detect_nds(file_path, base_name, data, ext, total_size=size)
        if ext in [".z64", ".n64", ".v64"]:
            return cls._detect_n64(file_path, base_name, data, ext)

        return None

    @classmethod
    def _detect_nes_fds(cls, file_path: str, base_name: str, data: bytes, ext: str, total_size: Optional[int] = None) -> RomAnalysisResult:
        """NES / FDS 헤더 분석"""
        fds_signature = data.startswith(b"FDS\x1a") or b"*NINTENDO-HVC*" in data[:0x100]
        if fds_signature or ext == ".fds":
            evidence_method = "fds_header" if fds_signature else "extension_hint"
            evidence_conf = 0.98 if fds_signature else 0.60
            evidence_detail = "FDS disk header/signature matched" if fds_signature else "identified from .fds extension only"
            # 패미컴 디스크 시스템
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=len(data),
                file_ext=ext,
                system_id="fds",
                system_name="Nintendo Family Computer Disk System",
                system_type="console",
                platform_slug="fds",
                libretro_system="Nintendo_-_Family_Computer_Disk_System",
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="high",
                bios_info=BiosInfo(
                    needs_bios=True,
                    mandatory=True,
                    bios_files=["disksys.rom"],
                    description="FDS 구동을 위한 패미컴 디스크 시스템 바이오스 (disksys.rom)"
                ),
                header_metadata=HeaderMetadata(
                    title=os.path.splitext(base_name)[0],
                    header_type="FDS Disk Header"
                ),
                **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
                summary="패미컴 디스크 시스템 (FDS) 롬 (disksys.rom 바이오스 필요)"
            )

        # iNES / NES 2.0 헤더
        mapper = 0
        header_type = "iNES"
        nes_signature = len(data) >= 16 and data.startswith(b"NES\x1a")
        size_sane = False
        expected_size = None
        warnings = []
        if nes_signature:
            prg_banks = data[4]
            chr_banks = data[5]
            flags6 = data[6]
            flags7 = data[7]
            mapper = (flags6 >> 4) | (flags7 & 0xF0)
            if (flags7 & 0x0C) == 0x08:
                header_type = "NES 2.0"
            # NES 2.0 exponent/multiplier sizes require extra decoding; basic iNES sizes
            # are still useful as a lower-bound sanity check when MSB nibbles are zero.
            trainer_size = 512 if (flags6 & 0x04) else 0
            if header_type == "iNES" or (data[9] & 0x0F) == 0:
                expected_size = 16 + trainer_size + prg_banks * 16384 + chr_banks * 8192
                actual_size = total_size or len(data)
                size_sane = prg_banks > 0 and actual_size >= expected_size
                if not size_sane:
                    warnings.append(f"NES 헤더 선언 크기({expected_size} bytes)가 실제 파일 크기({actual_size} bytes)와 맞지 않습니다.")

        if nes_signature and size_sane:
            evidence_method, evidence_conf = "nes_header_size_valid", 0.99
            evidence_detail = f"{header_type} magic and declared PRG/CHR size validated"
        elif nes_signature:
            evidence_method, evidence_conf = "nes_header", 0.88
            evidence_detail = f"{header_type} magic matched but declared ROM size was not validated"
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension only"
        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=len(data),
            file_ext=ext,
            system_id="nes",
            system_name="Nintendo Entertainment System (NES)",
            system_type="console",
            platform_slug="nes",
            libretro_system="Nintendo_-_Nintendo_Entertainment_System",
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            header_metadata=HeaderMetadata(
                title=os.path.splitext(base_name)[0],
                header_type=header_type,
                cartridge_hardware=f"Mapper {mapper}" if mapper else "Standard MMC"
            ),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"닌텐도 NES/패미컴 카트리지 롬 ({header_type}, Mapper {mapper})",
            warnings=warnings
        )

    @classmethod
    def _detect_snes(cls, file_path: str, base_name: str, data: bytes, ext: str, total_size: Optional[int] = None) -> RomAnalysisResult:
        """SNES (Super Nintendo / Super Famicom) 내부 헤더 분석"""
        size = len(data)
        eff_size = total_size or size
        offset = 512 if (eff_size % 1024 == 512) else 0  # SMC 복사기 헤더 512바이트 보정

        def _score_snes_header(pos):
            if pos + 0x40 > size:
                return -1
            title_bytes = data[pos:pos+21]
            ascii_chars = sum(1 for b in title_bytes if 0x20 <= b <= 0x7E)
            chk, chk_inv = struct.unpack("<HH", data[pos+0x1C:pos+0x20])
            score = ascii_chars
            if (chk ^ chk_inv) == 0xFFFF:
                score += 50
            return score

        candidate_positions = [
            offset + 0xFFC0,
            offset + 0x7FC0,
            0x101C0,
            0x81C0,
            0xFFC0,
            0x7FC0,
            0x40FFC0 if size > 0x40FFC0 else 0xFFC0
        ]
        best_pos = candidate_positions[0]
        best_score = -1
        for pos in candidate_positions:
            s = _score_snes_header(pos)
            if s > best_score:
                best_score = s
                best_pos = pos

        chosen_pos = best_pos
        if best_score >= 50:
            evidence_method, evidence_conf = "snes_checksum_header", 0.97
            evidence_detail = f"SNES internal header checksum complement validated at 0x{chosen_pos:X}"
        elif best_score >= 14:
            evidence_method, evidence_conf = "snes_header_heuristic", 0.78
            evidence_detail = f"plausible SNES internal header found at 0x{chosen_pos:X}"
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension; internal header not validated"
        coprocessor = "Standard"
        title = None
        region = None

        if chosen_pos + 0x30 <= size:
            title = _clean_str(data[chosen_pos:chosen_pos+21])
            rom_type = data[chosen_pos+0x16] if chosen_pos+0x16 < size else 0
            country_code = data[chosen_pos+0x19] if chosen_pos+0x19 < size else 0

            # 특수 칩 판별
            if rom_type in [0x13, 0x14, 0x15, 0x1A]:
                coprocessor = "SuperFX"
            elif rom_type in [0x34, 0x35]:
                coprocessor = "SA-1"
            elif rom_type in [0x03, 0x04, 0x05] and b"DSP" in data[chosen_pos:chosen_pos+21]:
                coprocessor = "DSP-1/2/3/4"
            elif rom_type == 0x43:
                coprocessor = "S-DD1"
            elif rom_type == 0x45:
                coprocessor = "SPC7110"

            region_map = {0: "Japan (NTSC)", 1: "North America (NTSC)", 2: "Europe (PAL)", 13: "Korea (NTSC)"}
            region = region_map.get(country_code, "Worldwide")

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=size,
            file_ext=ext,
            system_id="snes",
            system_name="Super Nintendo Entertainment System (SNES)",
            system_type="console",
            platform_slug="snes",
            libretro_system="Nintendo_-_Super_Nintendo_Entertainment_System",
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            header_metadata=HeaderMetadata(
                title=title or os.path.splitext(base_name)[0],
                region=region,
                header_type="SNES Internal Header",
                cartridge_hardware=coprocessor if coprocessor != "Standard" else None
            ),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"슈퍼 패미컴 (SNES) 카트리지 롬 (칩셋: {coprocessor})"
        )

    @classmethod
    def _detect_gb(cls, file_path: str, base_name: str, data: bytes, ext: str) -> RomAnalysisResult:
        """Game Boy / Game Boy Color 헤더 분석"""
        size = len(data)
        title = None
        is_gbc = ext == ".gbc"

        logo_valid = size >= 0x150 and data[0x104:0x134] == GB_NINTENDO_LOGO
        checksum_calc = _gb_header_checksum(data)
        checksum_valid = checksum_calc is not None and checksum_calc == data[0x14D]
        if size >= 0x150:
            title = _clean_str(data[0x134:0x144])
            cgb_flag = data[0x143]
            if cgb_flag in [0x80, 0xC0]:
                is_gbc = True

        system_id = "gbc" if is_gbc else "gb"
        system_name = "Nintendo Game Boy Color" if is_gbc else "Nintendo Game Boy"
        libretro_sys = "Nintendo_-_Game_Boy_Color" if is_gbc else "Nintendo_-_Game_Boy"
        if logo_valid and checksum_valid:
            evidence_method, evidence_conf = "gb_header_checksum", 0.995
            evidence_detail = f"Nintendo logo and header checksum validated; CGB flag={'yes' if is_gbc else 'no'}"
        elif logo_valid:
            evidence_method, evidence_conf = "gb_nintendo_logo", 0.97
            evidence_detail = f"Nintendo cartridge logo validated; header checksum mismatch; CGB flag={'yes' if is_gbc else 'no'}"
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension; Nintendo logo not validated"

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=size,
            file_ext=ext,
            system_id=system_id,
            system_name=system_name,
            system_type="handheld",
            platform_slug=system_id,
            libretro_system=libretro_sys,
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            header_metadata=HeaderMetadata(
                title=title or os.path.splitext(base_name)[0],
                header_type="GameBoy Header",
                internal_checksum=(f"{data[0x14D]:02x}" if size > 0x14D else None)
            ),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"{system_name} 롬 카트리지"
        )

    @classmethod
    def _detect_gba(cls, file_path: str, base_name: str, data: bytes, ext: str) -> RomAnalysisResult:
        """Game Boy Advance 헤더 분석"""
        size = len(data)
        title = None
        game_code = None

        if size >= 0xC0:
            title = _clean_str(data[0xA0:0xAC])
            game_code = _clean_str(data[0xAC:0xB0])

        serial = f"AGB-{game_code}" if game_code else None
        db_info = query_disc_serial(serial) if serial else None
        final_title = (db_info.get("title") if db_info else None) or title or os.path.splitext(base_name)[0]
        fixed_value_valid = size > 0xB2 and data[0xB2] == 0x96
        logo_valid = size >= 0xA0 and data[0x04:0xA0] == GBA_NINTENDO_LOGO
        checksum_calc = _gba_header_checksum(data)
        checksum_valid = checksum_calc is not None and size > 0xBD and checksum_calc == data[0xBD]
        code_valid = bool(game_code and len(game_code) == 4 and game_code.isalnum())
        if logo_valid and fixed_value_valid and checksum_valid and code_valid:
            evidence_method, evidence_conf = "gba_header_checksum", 0.995
            evidence_detail = f"GBA Nintendo logo, fixed value, game code and header checksum validated: {game_code}"
        elif fixed_value_valid and code_valid:
            evidence_method, evidence_conf = "gba_header", 0.92
            evidence_detail = f"GBA fixed header value and game code validated, but logo/checksum validation was incomplete: {game_code}"
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension; GBA header validation incomplete"

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=size,
            file_ext=ext,
            system_id="gba",
            system_name="Nintendo Game Boy Advance",
            system_type="handheld",
            platform_slug="gba",
            libretro_system="Nintendo_-_Game_Boy_Advance",
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            bios_info=BiosInfo(needs_bios=False, mandatory=False, bios_files=["gba_bios.bin"], description="GBA 공식 바이오스 권장 (HLE 구동 가능)"),
            header_metadata=HeaderMetadata(
                title=final_title,
                serial=serial,
                region=db_info.get("region") if db_info else None,
                header_type="GBA Header",
                internal_checksum=(f"{data[0xBD]:02x}" if size > 0xBD else None)
            ),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"게임보이 어드밴스 (GBA) 롬: {final_title} (시리얼: {serial or 'N/A'})"
        )

    @classmethod
    def _detect_nds(cls, file_path: str, base_name: str, data: bytes, ext: str, total_size: Optional[int] = None) -> RomAnalysisResult:
        """Nintendo DS 헤더 분석"""
        size = len(data)
        title = None
        game_code = None

        if size >= 0x40:
            title = _clean_str(data[0x00:0x0C])
            game_code = _clean_str(data[0x0C:0x10])

        serial = f"NTR-{game_code}" if game_code else None
        db_info = query_disc_serial(serial) if serial else None
        final_title = (db_info.get("title") if db_info else None) or title or os.path.splitext(base_name)[0]
        code_valid = bool(game_code and len(game_code) == 4 and game_code.isalnum())
        logo_valid = size >= 0x160 and data[0xC0:0x15C] == GBA_NINTENDO_LOGO
        logo_crc_stored = int.from_bytes(data[0x15C:0x15E], "little") if size >= 0x15E else None
        header_crc_stored = int.from_bytes(data[0x15E:0x160], "little") if size >= 0x160 else None
        logo_crc_valid = bool(logo_valid and logo_crc_stored == _nintendo_crc16(data[0xC0:0x15C]))
        header_crc_valid = bool(size >= 0x160 and header_crc_stored == _nintendo_crc16(data[:0x15E]))

        arm9_offset = struct.unpack_from("<I", data, 0x20)[0] if size >= 0x30 else 0
        arm9_size = struct.unpack_from("<I", data, 0x2C)[0] if size >= 0x30 else 0
        arm7_offset = struct.unpack_from("<I", data, 0x30)[0] if size >= 0x40 else 0
        arm7_size = struct.unpack_from("<I", data, 0x3C)[0] if size >= 0x40 else 0
        actual_size = total_size or size
        arm_ranges_sane = bool(
            arm9_offset >= 0x200 and arm9_size > 0 and arm9_offset + arm9_size <= actual_size
            and arm7_offset >= 0x200 and arm7_size > 0 and arm7_offset + arm7_size <= actual_size
        )

        if code_valid and logo_crc_valid and header_crc_valid and arm_ranges_sane:
            evidence_method, evidence_conf = "nds_header_crc", 0.995
            evidence_detail = f"NDS logo/header CRC16 and ARM7/ARM9 ranges validated: {game_code}"
        elif code_valid and logo_crc_valid and header_crc_valid:
            evidence_method, evidence_conf = "nds_header_crc", 0.97
            evidence_detail = f"NDS logo/header CRC16 validated; ARM ranges were not fully validated: {game_code}"
        elif code_valid:
            evidence_method, evidence_conf = "nds_header", 0.86
            evidence_detail = f"NDS game code is plausible but CRC16 validation failed or was unavailable: {game_code}"
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension; NDS header validation incomplete"

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=size,
            file_ext=ext,
            system_id="nds",
            system_name="Nintendo DS",
            system_type="handheld",
            platform_slug="nds",
            libretro_system="Nintendo_-_Nintendo_DS",
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            bios_info=BiosInfo(needs_bios=False, mandatory=False, bios_files=["bios7.bin", "bios9.bin", "firmware.bin"], description="NDS 바이오스 및 펌웨어 (선택적)"),
            header_metadata=HeaderMetadata(
                title=final_title,
                serial=serial,
                region=db_info.get("region") if db_info else None,
                header_type="NDS Header",
                internal_checksum=(f"{header_crc_stored:04x}" if header_crc_stored is not None else None)
            ),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"닌텐도 DS (NDS) 롬: {final_title} (시리얼: {serial or 'N/A'})"
        )

    @classmethod
    def _detect_n64(cls, file_path: str, base_name: str, data: bytes, ext: str) -> RomAnalysisResult:
        """Nintendo 64 헤더 및 엔디안 분석"""
        size = len(data)
        endian_type = "Big-Endian (.z64)"
        title = None
        serial = None

        if size >= 0x40:
            magic = data[:4]
            if magic == b"\x37\x80\x40\x12":
                endian_type = "Byte-Swapped (.v64)"
                # 2바이트 스왑 변환 후 타이틀 읽기
                swapped = bytearray(data[:0x40])
                for i in range(0, len(swapped), 2):
                    swapped[i], swapped[i+1] = swapped[i+1], swapped[i]
                title = _clean_str(swapped[0x20:0x34])
                serial_code = _clean_str(swapped[0x3B:0x3F])
            elif magic == b"\x40\x12\x37\x80":
                endian_type = "Little-Endian (.n64)"
                # 4바이트 스왑
                swapped = bytearray(data[:0x40])
                for i in range(0, len(swapped), 4):
                    swapped[i:i+4] = reversed(swapped[i:i+4])
                title = _clean_str(swapped[0x20:0x34])
                serial_code = _clean_str(swapped[0x3B:0x3F])
            else:
                title = _clean_str(data[0x20:0x34])
                serial_code = _clean_str(data[0x3B:0x3F])

            if serial_code:
                serial = f"NUS-{serial_code}"

        db_info = query_disc_serial(serial) if serial else None
        final_title = (db_info.get("title") if db_info else None) or title or os.path.splitext(base_name)[0]
        magic_valid = size >= 4 and data[:4] in [b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"]
        if db_info:
            evidence_method, evidence_conf = "n64_serial_db", 0.99
            evidence_detail = f"N64 serial matched metadata DB: {serial}"
        elif magic_valid:
            evidence_method, evidence_conf = "n64_magic", 0.99
            evidence_detail = f"N64 byte-order magic validated: {data[:4].hex()}"
        else:
            evidence_method, evidence_conf = "extension_hint", 0.60
            evidence_detail = f"identified from {ext} extension; N64 magic not validated"

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=size,
            file_ext=ext,
            system_id="n64",
            system_name="Nintendo 64",
            system_type="console",
            platform_slug="n64",
            libretro_system="Nintendo_-_Nintendo_64",
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high",
            header_metadata=HeaderMetadata(
                title=final_title,
                serial=serial,
                region=db_info.get("region") if db_info else None,
                header_type=f"N64 Header ({endian_type})"
            ),
            **_evidence_kwargs(evidence_method, evidence_conf, evidence_detail),
            summary=f"닌텐도 64 (N64) 롬: {final_title} ({endian_type}, 시리얼: {serial or 'N/A'})"
        )
