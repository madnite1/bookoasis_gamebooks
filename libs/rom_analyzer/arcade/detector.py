# -*- coding: utf-8 -*-
"""
아케이드(MAME / FBNeo) ROM 아카이브 정밀 분석 및 탐지기.
ZIP / 7z 내부 파일 구조 분석, 콘솔 압축 여부 분기, 기판 판별, 바이오스 필요 여부 판단.
"""

import os
import re
import zipfile
import logging
from typing import Optional, Tuple, List, Dict, Any

from ..models import RomAnalysisResult, ArcadeInfo, BiosInfo, HeaderMetadata, DetectionEvidence
from ..db import query_arcade_romset
from .bios_db import ARCADE_BIOS_SETS, ARCADE_DEVICE_SETS
from .database import ARCADE_GAMES_CATALOG, BOARD_PREFIX_PATTERNS, lookup_arcade_catalog
from .dat_matcher import DatMatcher

logger = logging.getLogger(__name__)

# 콘솔 단일 롬이 압축되어 있는 확장자 목록
CONSOLE_EXTENSIONS = {
    ".nes", ".fds", ".unf",
    ".sfc", ".smc", ".swc", ".fig",
    ".md", ".smd", ".gen", ".32x", ".sms", ".gg", ".sg",
    ".gb", ".gbc", ".gba", ".nds", ".ds", ".3ds",
    ".n64", ".z64", ".v64",
    ".pce", ".sgx", ".cue", ".iso", ".chd", ".gdi", ".pbp",
    ".ws", ".wsc", ".ngp", ".ngc", ".ngpc",
    ".a26", ".a78", ".lnx", ".j64"
}

# 네오지오 칩 파일 패턴 (p1, p2, m1, s1, c1~c8, v1~v4 등)
NEOGEO_CHIP_RE = re.compile(r"^(.*)[\.\-_](p[0-9]|m[0-9]|s[0-9]|c[0-9]|v[0-9]|sp2|lo|sma)$", re.IGNORECASE)

# CPS1 / CPS2 칩 파일 패턴 (숫자 번호 확장자: .01, .02, .03 등 또는 qsound 칩)
CPS_CHIP_RE = re.compile(r"^(.*)[\.\-_]([0-9]{1,2}|qsound|key)$", re.IGNORECASE)

# PGM 칩 패턴
PGM_CHIP_RE = re.compile(r"^(.*)[\.\-_](p[0-9]{2}s?|m[0-9]{2}s?|t[0-9]{2}s?|v[0-9]{2}s?|a[0-9]{2}s?|b[0-9]{2}s?)$", re.IGNORECASE)


class ArcadeDetector:
    """아케이드 롬 파일 탐지기"""

    @classmethod
    def inspect_archive(cls, file_path: str) -> Tuple[bool, Optional[str], List[str]]:
        """ZIP/7z 내부 파일 목록을 읽어 콘솔 래핑 여부와 파일 목록을 반환한다."""
        ext = os.path.splitext(file_path)[1].lower()
        namelist: List[str] = []
        try:
            if ext == ".zip":
                if not zipfile.is_zipfile(file_path):
                    return False, None, []
                with zipfile.ZipFile(file_path, "r") as z:
                    namelist = [f.filename for f in z.infolist() if not f.is_dir()]
            elif ext == ".7z":
                try:
                    import py7zr
                except ImportError:
                    logger.warning("7z 분석에는 py7zr 패키지가 필요합니다: %s", file_path)
                    return False, None, []
                with py7zr.SevenZipFile(file_path, mode="r") as z:
                    namelist = [n for n in z.getnames() if not n.endswith("/")]
            else:
                return False, None, []
        except Exception as exc:
            logger.debug("아카이브 내부 목록 조회 실패: %s (%s)", file_path, exc)
            return False, None, []

        if not namelist:
            return False, None, []
        for fname in namelist:
            if os.path.splitext(fname)[1].lower() in CONSOLE_EXTENSIONS:
                return True, fname, namelist
        return False, None, namelist

    @classmethod
    def inspect_zip_archive(cls, file_path: str) -> Tuple[bool, Optional[str], List[str]]:
        """하위 호환용 별칭. ZIP뿐 아니라 7z도 inspect_archive로 처리한다."""
        return cls.inspect_archive(file_path)

    @classmethod
    def detect(cls, file_path: str) -> Optional[RomAnalysisResult]:
        """
        주어진 파일(주로 .zip / .7z)이 아케이드 롬인지 분석.
        아케이드가 아니면 None 반환 (콘솔 분석기로 위임).
        """
        base_name = os.path.basename(file_path)
        rom_name, file_ext = os.path.splitext(base_name)
        rom_key = rom_name.lower().strip()
        file_ext_lower = file_ext.lower()

        # ZIP 또는 7z 또는 특수 아케이드 확장자가 아니면 아케이드 판별 스킵
        if file_ext_lower not in [".zip", ".7z"]:
            return None

        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

        # 1. BIOS 롬셋 여부 확인
        if rom_key in ARCADE_BIOS_SETS:
            bio = ARCADE_BIOS_SETS[rom_key]
            arcade_info = ArcadeInfo(
                is_arcade=True,
                driver=rom_key,
                board=bio.get("board", "Arcade System Board"),
                parent_rom=None,
                is_clone=False,
                is_bios_set=True,
                is_device_set=False,
                needs_bios=False,
                required_bios=[],
                needs_chd=False,
                recommended_cores=["fbneo", "mame"]
            )
            bios_info = BiosInfo(
                needs_bios=False,
                mandatory=True,
                bios_files=[f"{rom_key}.zip"],
                description=bio.get("description", "아케이드 시스템 필수 바이오스 세트")
            )
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=file_size,
                file_ext=file_ext,
                system_id=bio.get("system_id", "arcade"),
                system_name=f"{bio.get('name', 'Arcade BIOS')} (System BIOS)",
                system_type="arcade",
                platform_slug=bio.get("platform_slug", "arcade"),
                libretro_system=bio.get("libretro_system", "MAME"),
                is_arcade=True,
                is_disc=False,
                is_playable=False,  # 바이오스 파일 자체는 직접 플레이 불가
                confidence="high",
                arcade_info=arcade_info,
                bios_info=bios_info,
                header_metadata=HeaderMetadata(title=bio.get("name")),
                confidence_score=0.99,
                evidence=[DetectionEvidence(
                    method="arcade_bios_catalog", confidence=0.99,
                    detail=f"exact arcade BIOS set name matched: {rom_key}", source="arcade_bios_db"
                )],
                detection_methods=["arcade_bios_catalog"],
                summary=f"아케이드 시스템 바이오스 세트: {bio.get('name')} (단독 플레이 불가, 해당 기종 게임 구동 필수 파일)"
            )

        # 2. 장치 / 사운드칩 롬셋 여부 확인
        if rom_key in ARCADE_DEVICE_SETS:
            dev = ARCADE_DEVICE_SETS[rom_key]
            arcade_info = ArcadeInfo(
                is_arcade=True,
                driver=rom_key,
                board=dev.get("board", "Arcade Device Hardware"),
                is_bios_set=False,
                is_device_set=True,
                recommended_cores=["fbneo", "mame"]
            )
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=file_size,
                file_ext=file_ext,
                system_id="arcade",
                system_name=f"{dev.get('name', 'Arcade Device')} (Device ROM)",
                system_type="arcade",
                platform_slug="arcade",
                libretro_system="MAME",
                is_arcade=True,
                is_playable=False,
                confidence="high",
                confidence_score=0.99,
                arcade_info=arcade_info,
                evidence=[DetectionEvidence(
                    method="arcade_device_catalog", confidence=0.99,
                    detail=f"exact arcade device set name matched: {rom_key}", source="arcade_bios_db"
                )],
                detection_methods=["arcade_device_catalog"],
                summary=f"아케이드 보조 장치/음원 롬셋: {dev.get('name')}"
            )

        # 3. DAT/CRC 증거를 우선 수집한다. 콘솔 softlist 매치라면 아케이드 분석에서 제외한다.
        dat_match = DatMatcher.match_archive(file_path)
        if dat_match and dat_match.platform and dat_match.platform.lower() != "arcade":
            return None

        # 명시적인 콘솔 내부 확장자는 파일명/카탈로그 추정보다 강하다.
        # 단, 실제 아케이드 DAT CRC가 확인된 경우에는 DAT 증거를 우선한다.
        is_console, inner_fname, inner_files = cls.inspect_archive(file_path)
        has_arcade_crc = bool(
            dat_match
            and dat_match.platform.lower() == "arcade"
            and dat_match.matched_count > 0
        )
        if is_console and inner_fname and not has_arcade_crc:
            return None

        # 4. 아케이드 게임 롬셋 데이터베이스 조회 (SQLite DB 우선 조회 -> 메모리 카탈로그 폴백)
        catalog_key = dat_match.rom_name if dat_match and dat_match.rom_name else rom_key
        catalog_info = query_arcade_romset(catalog_key) or lookup_arcade_catalog(catalog_key)

        board_name = None
        game_title = None
        parent_rom = None
        is_clone = False
        required_bios: List[str] = []
        needs_chd = False
        chd_name = None
        recommended_cores = ["fbneo", "mame2003_plus", "mame"]

        if catalog_info:
            game_title = catalog_info.get("title")
            board_name = catalog_info.get("board")
            parent_rom = catalog_info.get("parent")
            is_clone = catalog_info.get("is_clone", bool(parent_rom))
            required_bios = catalog_info.get("bios", [])
            needs_chd = catalog_info.get("chd", False)
            chd_name = catalog_info.get("chd_name")
            recommended_cores = catalog_info.get("cores", recommended_cores)
        else:
            # 카탈로그에 없는 경우 내부 칩 파일 및 파일명 정규식으로 기판 추론
            board_name, inferred_bios = cls._infer_board_from_files(rom_key, inner_files)
            if inferred_bios and inferred_bios not in required_bios:
                required_bios.append(inferred_bios)

        # DAT는 파일명보다 강한 식별 근거이며, 카탈로그에 없는 롬셋도 식별 가능하게 한다.
        if dat_match:
            game_title = dat_match.title or game_title
            parent_rom = dat_match.parent_rom or parent_rom
            is_clone = dat_match.is_clone or is_clone
            if dat_match.romof and dat_match.romof in ARCADE_BIOS_SETS:
                dep = f"{dat_match.romof}.zip"
                if dep not in required_bios:
                    required_bios.append(dep)

        # 아케이드 판별이 되었거나, 내부 칩 파일들이 전형적인 아케이드 롬 구조인 경우
        if dat_match or catalog_info or board_name or (inner_files and cls._has_arcade_rom_structure(inner_files)):
            final_board = board_name or "Arcade (MAME / FBNeo Standard PCB)"

            # 네오지오 기판인 경우 neogeo.zip 바이오스 필수 설정
            if "Neo-Geo" in final_board and "neogeo.zip" not in required_bios:
                required_bios.append("neogeo.zip")
            # PGM 기판인 경우 pgm.zip 바이오스 필수 설정
            if "PGM" in final_board and "pgm.zip" not in required_bios:
                required_bios.append("pgm.zip")
            # NAOMI 기판인 경우 naomi.zip 바이오스 필수 설정
            if "NAOMI" in final_board and "naomi.zip" not in required_bios:
                required_bios.append("naomi.zip")
            # ST-V 기판인 경우 stv.zip 바이오스 필수 설정
            if "ST-V" in final_board and "stv.zip" not in required_bios:
                required_bios.append("stv.zip")
            # Atomiswave 기판인 경우 awbios.zip 바이오스 필수 설정
            if "Atomiswave" in final_board and "awbios.zip" not in required_bios:
                required_bios.append("awbios.zip")

            has_bios = len(required_bios) > 0

            # 플랫폼 슬러그 및 시스템 결정
            if "Neo-Geo" in final_board:
                system_id = "neogeo"
                platform_slug = "neogeo"
                libretro_system = "SNK_-_Neo_Geo"
                system_name = f"SNK Neo-Geo MVS [{game_title or rom_name}]"
            elif "CPS-1" in final_board or "CPS-2" in final_board or "CPS-3" in final_board:
                system_id = "arcade"
                platform_slug = "arcade"
                libretro_system = "MAME"
                system_name = f"{final_board} [{game_title or rom_name}]"
            elif "NAOMI" in final_board:
                system_id = "naomi"
                platform_slug = "naomi"
                libretro_system = "Sega_-_NAOMI"
                system_name = f"Sega NAOMI [{game_title or rom_name}]"
            elif "Atomiswave" in final_board:
                system_id = "atomiswave"
                platform_slug = "atomiswave"
                libretro_system = "Sammy_-_Atomiswave"
                system_name = f"Sammy Atomiswave [{game_title or rom_name}]"
            else:
                system_id = "arcade"
                platform_slug = "arcade"
                libretro_system = "MAME"
                system_name = f"Arcade ({final_board}) [{game_title or rom_name}]"

            arcade_info = ArcadeInfo(
                is_arcade=True,
                driver=rom_key,
                board=final_board,
                parent_rom=parent_rom,
                is_clone=is_clone,
                is_bios_set=False,
                is_device_set=False,
                needs_bios=has_bios,
                required_bios=required_bios,
                needs_chd=needs_chd,
                chd_name=chd_name,
                matched_count=dat_match.matched_count if dat_match else 0,
                total_roms=dat_match.total_roms if dat_match else 0,
                match_rate=dat_match.match_rate if dat_match else 0.0,
                archive_match_rate=dat_match.archive_match_rate if dat_match else 0.0,
                dat_system=dat_match.system_name if dat_match else None,
                dat_status=dat_match.status if dat_match else None,
                dat_score=dat_match.score if dat_match else 0.0,
                dat_candidate_count=dat_match.candidate_count if dat_match else 0,
                recommended_cores=recommended_cores
            )

            bios_info = BiosInfo(
                needs_bios=has_bios,
                mandatory=has_bios,
                bios_files=required_bios,
                description=f"구동을 위해 다음 바이오스 롬셋이 필요합니다: {', '.join(required_bios)}" if has_bios else "별도 바이오스 불필요"
            )

            summary_parts = [f"아케이드 롬 ({final_board})"]
            if is_clone and parent_rom:
                summary_parts.append(f"부모 롬: {parent_rom}")
            if has_bios:
                summary_parts.append(f"필수 바이오스: {', '.join(required_bios)}")
            if needs_chd:
                summary_parts.append("추가 CHD 디스크 이미지 필요")
            if dat_match:
                summary_parts.append(
                    f"DAT {dat_match.status}: CRC {dat_match.matched_count}/{dat_match.total_roms} "
                    f"(DAT {dat_match.match_rate:.1f}%, archive {dat_match.archive_match_rate:.1f}%)"
                )

            evidence = []
            if dat_match and dat_match.status == "ambiguous":
                top = ", ".join(f"{c.rom_name}@{c.system_name}:{c.score:.3f}" for c in dat_match.candidates[:3])
                # Keep the best candidate for compatibility, but expose uncertainty explicitly.
                summary_parts.append(f"DAT 후보 경합: {top}")
            if dat_match:
                evidence.append(DetectionEvidence(
                    method="dat_crc" if dat_match.matched_count else "dat_name",
                    confidence=dat_match.confidence_score,
                    detail=(
                        f"{dat_match.status}/{dat_match.match_basis}: {dat_match.matched_count}/{dat_match.total_roms} ROM CRC matched; "
                        f"DAT coverage={dat_match.match_rate:.2f}%, archive coverage={dat_match.archive_match_rate:.2f}%, "
                        f"rank score={dat_match.score:.3f}"
                    ),
                    source=dat_match.system_name,
                ))
            elif catalog_info:
                evidence.append(DetectionEvidence(
                    method="romset_name",
                    confidence=0.90,
                    detail=f"embedded arcade catalog exact match: {catalog_key}",
                    source="rom_metadata.db",
                ))
            elif board_name:
                evidence.append(DetectionEvidence(
                    method="archive_structure",
                    confidence=0.68,
                    detail=f"archive chip layout inferred board: {final_board}",
                    source="archive",
                ))

            result = RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=file_size,
                file_ext=file_ext,
                system_id=system_id,
                system_name=system_name,
                system_type="arcade",
                platform_slug=platform_slug,
                libretro_system=libretro_system,
                is_arcade=True,
                is_disc=False,
                is_playable=True,
                confidence="high" if (dat_match or catalog_info) else "medium",
                confidence_score=(dat_match.confidence_score if dat_match else (0.90 if catalog_info else 0.68)),
                arcade_info=arcade_info,
                bios_info=bios_info,
                header_metadata=HeaderMetadata(title=game_title or rom_name),
                evidence=evidence,
                summary=" | ".join(summary_parts)
            )
            if dat_match and dat_match.status == "ambiguous":
                result.add_warning("DAT CRC 후보 점수가 근접하여 게임 식별이 모호합니다.")
            return result

        return None

    @classmethod
    def _infer_board_from_files(cls, rom_key: str, inner_files: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """내부 파일 목록 및 롬 키 패턴으로 기판 종류 및 바이오스 추론"""
        # 1. 파일명 정규식 패턴 검사
        for pattern, board in BOARD_PREFIX_PATTERNS:
            if re.match(pattern, rom_key, re.IGNORECASE):
                if "Neo-Geo" in board:
                    return board, "neogeo.zip"
                if "PGM" in board:
                    return board, "pgm.zip"
                if "NAOMI" in board:
                    return board, "naomi.zip"
                if "ST-V" in board:
                    return board, "stv.zip"
                if "Atomiswave" in board:
                    return board, "awbios.zip"
                return board, None

        if not inner_files:
            return None, None

        # 2. 내부 칩 확장자 분석
        has_neogeo_chips = any(NEOGEO_CHIP_RE.match(f) for f in inner_files)
        if has_neogeo_chips:
            return "SNK Neo-Geo MVS", "neogeo.zip"

        has_pgm_chips = any(PGM_CHIP_RE.match(f) for f in inner_files)
        if has_pgm_chips:
            return "IGS PolyGame Master (PGM)", "pgm.zip"

        # CPS2 내부 칩 번호 (.03, .04, .05, .06 ...)
        cps_chips = [f for f in inner_files if CPS_CHIP_RE.match(f)]
        if len(cps_chips) >= 3:
            return "Capcom CPS-2", "qsound_hle.zip"

        return None, None

    @classmethod
    def _has_arcade_rom_structure(cls, inner_files: List[str]) -> bool:
        """내부 파일 목록이 전형적인 MAME/FBNeo 롬 칩 덤프 구조인지 확인"""
        if not inner_files:
            return False

        # 아케이드 롬은 보통 여러 개의 롬칩 바이너리 파일(4개 이상)로 분할되어 있음
        if len(inner_files) >= 3:
            # 롬칩 확장자 확인 (.bin, .rom, .dat, .ic*, .u*, .0~9)
            chip_count = 0
            for f in inner_files:
                ext = os.path.splitext(f)[1].lower()
                name = os.path.basename(f).lower()
                if ext in [".bin", ".rom", ".dat", ".ic", ".u", ".prg", ".chr", ".p1", ".m1", ".s1", ".c1", ".v1"] or ext[1:].isdigit():
                    chip_count += 1
                elif "ic" in name or "prom" in name or "eprom" in name:
                    chip_count += 1

            if chip_count >= len(inner_files) * 0.6:
                return True

        return False
