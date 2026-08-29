# -*- coding: utf-8 -*-
"""
ROM Analyzer 메인 오케스트레이터 및 분석 엔진.
주어진 파일 경로로부터 정확한 기종(아케이드, 콘솔, 핸드헬드), 기판 종류,
바이오스 필요 여부, 디스크 보조 파일 및 트랙 완전성 등을 정밀 진단.
"""

import os
import zlib
import hashlib
from typing import Union, Optional, Dict, Any
from pathlib import Path

from .models import RomAnalysisResult, ArcadeInfo, DiscInfo, BiosInfo, HeaderMetadata, DetectionEvidence
from .arcade.detector import ArcadeDetector
from .arcade.dat_matcher import DatMatcher
from .disc.inspector import DiscInspector
from .headers.detector import ConsoleHeaderDetector
from .evidence import EvidenceScorer
from .core_info import CoreInfoManager


# 일반 확장자 기반 기본 시스템 매핑 테이블 (헤더 미인식 시 폴백)
GENERIC_EXT_MAP: Dict[str, Dict[str, Any]] = {
    ".nes": {"id": "nes", "name": "Nintendo Entertainment System", "type": "console", "slug": "nes", "libretro": "Nintendo_-_Nintendo_Entertainment_System"},
    ".fds": {"id": "fds", "name": "Nintendo FDS", "type": "console", "slug": "fds", "libretro": "Nintendo_-_Family_Computer_Disk_System"},
    ".sfc": {"id": "snes", "name": "Super Nintendo Entertainment System", "type": "console", "slug": "snes", "libretro": "Nintendo_-_Super_Nintendo_Entertainment_System"},
    ".smc": {"id": "snes", "name": "Super Nintendo Entertainment System", "type": "console", "slug": "snes", "libretro": "Nintendo_-_Super_Nintendo_Entertainment_System"},
    ".gb": {"id": "gb", "name": "Nintendo Game Boy", "type": "handheld", "slug": "gb", "libretro": "Nintendo_-_Game_Boy"},
    ".gbc": {"id": "gbc", "name": "Nintendo Game Boy Color", "type": "handheld", "slug": "gbc", "libretro": "Nintendo_-_Game_Boy_Color"},
    ".gba": {"id": "gba", "name": "Nintendo Game Boy Advance", "type": "handheld", "slug": "gba", "libretro": "Nintendo_-_Game_Boy_Advance"},
    ".nds": {"id": "nds", "name": "Nintendo DS", "type": "handheld", "slug": "nds", "libretro": "Nintendo_-_Nintendo_DS"},
    ".n64": {"id": "n64", "name": "Nintendo 64", "type": "console", "slug": "n64", "libretro": "Nintendo_-_Nintendo_64"},
    ".z64": {"id": "n64", "name": "Nintendo 64", "type": "console", "slug": "n64", "libretro": "Nintendo_-_Nintendo_64"},
    ".v64": {"id": "n64", "name": "Nintendo 64", "type": "console", "slug": "n64", "libretro": "Nintendo_-_Nintendo_64"},
    ".md": {"id": "megadrive", "name": "Sega Mega Drive / Genesis", "type": "console", "slug": "megadrive", "libretro": "Sega_-_Mega_Drive_-_Genesis"},
    ".gen": {"id": "megadrive", "name": "Sega Mega Drive / Genesis", "type": "console", "slug": "megadrive", "libretro": "Sega_-_Mega_Drive_-_Genesis"},
    ".smd": {"id": "megadrive", "name": "Sega Mega Drive / Genesis", "type": "console", "slug": "megadrive", "libretro": "Sega_-_Mega_Drive_-_Genesis"},
    ".32x": {"id": "sega32x", "name": "Sega 32X", "type": "console", "slug": "sega32x", "libretro": "Sega_-_32X"},
    ".sms": {"id": "mastersystem", "name": "Sega Master System", "type": "console", "slug": "mastersystem", "libretro": "Sega_-_Master_System_-_Mark_III"},
    ".gg": {"id": "gamegear", "name": "Sega Game Gear", "type": "handheld", "slug": "gamegear", "libretro": "Sega_-_Game_Gear"},
    ".pce": {"id": "pce", "name": "NEC PC Engine", "type": "console", "slug": "pce", "libretro": "NEC_-_PC_Engine_-_TurboGrafx_16"},
    ".sgx": {"id": "supergrafx", "name": "NEC SuperGrafx", "type": "console", "slug": "supergrafx", "libretro": "NEC_-_PC_Engine_SuperGrafx"},
    ".ws": {"id": "wonderswan", "name": "Bandai WonderSwan", "type": "handheld", "slug": "wonderswan", "libretro": "Bandai_-_WonderSwan"},
    ".wsc": {"id": "wsc", "name": "Bandai WonderSwan Color", "type": "handheld", "slug": "wsc", "libretro": "Bandai_-_WonderSwan_Color"},
    ".ngp": {"id": "ngp", "name": "SNK Neo Geo Pocket", "type": "handheld", "slug": "ngp", "libretro": "SNK_-_Neo_Geo_Pocket"},
    ".ngpc": {"id": "ngpc", "name": "SNK Neo Geo Pocket Color", "type": "handheld", "slug": "ngpc", "libretro": "SNK_-_Neo_Geo_Pocket_Color"},
    ".a26": {"id": "atari2600", "name": "Atari 2600", "type": "console", "slug": "atari2600", "libretro": "Atari_-_2600"},
    ".a78": {"id": "atari7800", "name": "Atari 7800", "type": "console", "slug": "atari7800", "libretro": "Atari_-_7800"},
    ".lnx": {"id": "lynx", "name": "Atari Lynx", "type": "handheld", "slug": "lynx", "libretro": "Atari_-_Lynx"},
    ".j64": {"id": "jaguar", "name": "Atari Jaguar", "type": "console", "slug": "jaguar", "libretro": "Atari_-_Jaguar"},
    ".jag": {"id": "jaguar", "name": "Atari Jaguar", "type": "console", "slug": "jaguar", "libretro": "Atari_-_Jaguar"},
    ".a52": {"id": "atari5200", "name": "Atari 5200", "type": "console", "slug": "atari5200", "libretro": "Atari_-_5200"},
    ".vb": {"id": "vb", "name": "Nintendo Virtual Boy", "type": "console", "slug": "virtualboy", "libretro": "Nintendo_-_Virtual_Boy"},
    ".vboy": {"id": "vb", "name": "Nintendo Virtual Boy", "type": "console", "slug": "virtualboy", "libretro": "Nintendo_-_Virtual_Boy"},
    ".col": {"id": "coleco", "name": "ColecoVision", "type": "console", "slug": "coleco", "libretro": "Coleco_-_ColecoVision"},
    ".cv": {"id": "coleco", "name": "ColecoVision", "type": "console", "slug": "coleco", "libretro": "Coleco_-_ColecoVision"},
}


_DAT_PLATFORM_MAP: Dict[str, Dict[str, str]] = {
    "NES": {"id": "nes", "name": "Nintendo Entertainment System", "type": "console", "slug": "nes", "libretro": "Nintendo_-_Nintendo_Entertainment_System"},
    "SNES": {"id": "snes", "name": "Super Nintendo Entertainment System", "type": "console", "slug": "snes", "libretro": "Nintendo_-_Super_Nintendo_Entertainment_System"},
    "GB": {"id": "gb", "name": "Nintendo Game Boy", "type": "handheld", "slug": "gb", "libretro": "Nintendo_-_Game_Boy"},
    "GBA": {"id": "gba", "name": "Nintendo Game Boy Advance", "type": "handheld", "slug": "gba", "libretro": "Nintendo_-_Game_Boy_Advance"},
    "Genesis": {"id": "megadrive", "name": "Sega Mega Drive / Genesis", "type": "console", "slug": "megadrive", "libretro": "Sega_-_Mega_Drive_-_Genesis"},
    "N64": {"id": "n64", "name": "Nintendo 64", "type": "console", "slug": "n64", "libretro": "Nintendo_-_Nintendo_64"},
    "GameGear": {"id": "gamegear", "name": "Sega Game Gear", "type": "handheld", "slug": "gamegear", "libretro": "Sega_-_Game_Gear"},
    "SMS": {"id": "mastersystem", "name": "Sega Master System", "type": "console", "slug": "mastersystem", "libretro": "Sega_-_Master_System_-_Mark_III"},
    "PCE": {"id": "pce", "name": "NEC PC Engine / TurboGrafx-16", "type": "console", "slug": "pce", "libretro": "NEC_-_PC_Engine_-_TurboGrafx_16"},
}

_CONSOLE_DAT_EXTENSIONS = {
    ".nes", ".sfc", ".smc", ".gb", ".gbc", ".gba", ".nds",
    ".n64", ".z64", ".v64", ".md", ".gen", ".smd", ".32x",
    ".sms", ".gg", ".pce", ".sgx", ".ws", ".wsc", ".ngp", ".ngpc",
}


class RomAnalyzer:
    """통합 ROM 정밀 분석기 클래스"""

    @classmethod
    def analyze(cls, file_path: Union[str, Path], compute_hashes: bool = False) -> RomAnalysisResult:
        """
        주어진 롬 파일 경로를 정밀 분석하여 RomAnalysisResult 객체로 반환.

        :param file_path: 분석할 롬 파일 경로 (문자열 또는 Path)
        :param compute_hashes: CRC32, MD5, SHA1 해시 연산 수행 여부 (기본값: False, 필요 시 True)
        """
        abs_path = os.path.abspath(str(file_path))
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"ROM 파일을 찾을 수 없습니다: {abs_path}")

        file_name = os.path.basename(abs_path)
        file_size = os.path.getsize(abs_path)
        file_ext = os.path.splitext(file_name)[1].lower()

        result: Optional[RomAnalysisResult] = None

        # 1. .bin은 카트리지/펌웨어/디스크에 모두 쓰이므로 콘솔 헤더를 먼저 검사
        if file_ext == ".bin":
            result = ConsoleHeaderDetector.detect(abs_path)

        # 2. 디스크 기반 미디어 (CUE, GDI, CHD, ISO, PBP, CCD, MDS 등) 검사
        if not result and DiscInspector.is_disc_file(abs_path):
            result = DiscInspector.detect(abs_path)

        # 2. 아케이드 (MAME / FBNeo ZIP/7z) 아카이브 검사
        if not result and file_ext in [".zip", ".7z"]:
            result = ArcadeDetector.detect(abs_path)

        # 3. 아케이드로 식별되지 않은 다중 파일 ZIP/7z는 콘솔 DAT CRC 결과를 직접 사용한다.
        # 단일 콘솔 ROM ZIP은 다음 ConsoleHeaderDetector가 바이너리 헤더를 더 강한 근거로 판별한다.
        if not result and file_ext in {".zip", ".7z"}:
            result = cls._detect_console_archive_dat(abs_path)

        # 4. 콘솔 및 핸드헬드 카트리지 바이너리 헤더 검사 (ZIP 내 콘솔 롬 포함)
        if not result:
            result = ConsoleHeaderDetector.detect(abs_path)

        # 5. 일반 확장자 기반 폴백 검사
        if not result and file_ext in GENERIC_EXT_MAP:
            g = GENERIC_EXT_MAP[file_ext]
            result = RomAnalysisResult(
                file_path=abs_path,
                file_name=file_name,
                file_size=file_size,
                file_ext=file_ext,
                system_id=g["id"],
                system_name=g["name"],
                system_type=g["type"],
                platform_slug=g["slug"],
                libretro_system=g["libretro"],
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="medium",
                confidence_score=0.60,
                header_metadata=HeaderMetadata(title=os.path.splitext(file_name)[0]),
                evidence=[DetectionEvidence(
                    method="extension_hint", confidence=0.60,
                    detail=f"system inferred from {file_ext} extension only", source="generic_extension_map"
                )],
                detection_methods=["extension_hint"],
                summary=f"{g['name']} 롬 파일 ({file_ext})"
            )

        # 5. 미식별 파일 기본값
        if not result:
            result = RomAnalysisResult(
                file_path=abs_path,
                file_name=file_name,
                file_size=file_size,
                file_ext=file_ext,
                system_id="unknown",
                system_name="Unknown ROM / Media",
                system_type="unknown",
                platform_slug="unknown",
                is_arcade=False,
                is_disc=False,
                is_playable=False,
                confidence="low",
                confidence_score=0.25,
                header_metadata=HeaderMetadata(title=os.path.splitext(file_name)[0]),
                evidence=[DetectionEvidence(
                    method="unidentified", confidence=0.25,
                    detail="no supported binary signature, metadata match, or extension mapping identified the file",
                    source="rom_analyzer"
                )],
                detection_methods=["unidentified"],
                summary=f"식별되지 않은 미디어 파일 ({file_name})"
            )

        # 6. 콘솔/핸드헬드 결과에 DAT CRC 근거를 보강한다.
        if not result.is_arcade and not result.is_disc and result.system_id != "unknown":
            cls._apply_console_dat(abs_path, result)

        # 7. detector가 자체 근거를 제공하지 않은 기존 경로에도 최소 판별 근거를 부여한다.
        cls._ensure_evidence(result)

        # 8. 판별 근거의 최종 신뢰도/상태 계산 및 확장자 충돌 감지
        expected_system_id = GENERIC_EXT_MAP.get(file_ext, {}).get("id")
        EvidenceScorer.apply(result, expected_system_id=expected_system_id, file_ext=file_ext)

        # 9. EmulatorJS 지원 기종은 Stable 기준 BIOS 정의를 단일 소스로 적용한다.
        CoreInfoManager.apply_bios_info(result)

        # 10. EmulatorJS Stable 실행 호환성/추천 코어 계산
        CoreInfoManager.apply_emulatorjs_info(result)

        # 11. 해시 연산 (요청 시)
        if compute_hashes:
            cls._calc_hashes(abs_path, result)

        return result

    @classmethod
    def _detect_console_archive_dat(cls, file_path: str) -> Optional[RomAnalysisResult]:
        """다중 파일 콘솔 ZIP/7z를 DAT CRC 근거로 식별한다."""
        dat_match = DatMatcher.match_archive(file_path)
        if not dat_match or not dat_match.matched or not dat_match.platform:
            return None
        if dat_match.platform.lower() == "arcade" or dat_match.matched_count <= 0:
            return None
        if dat_match.status == "ambiguous":
            return None

        platform = _DAT_PLATFORM_MAP.get(dat_match.platform)
        if not platform:
            return None

        confidence_score = dat_match.confidence_score
        evidence = [DetectionEvidence(
            method="dat_crc",
            confidence=confidence_score,
            detail=(
                f"console archive DAT {dat_match.status}: {dat_match.matched_count}/{dat_match.total_roms} ROM CRC matched; "
                f"DAT coverage={dat_match.match_rate:.2f}%, archive coverage={dat_match.archive_match_rate:.2f}%"
            ),
            source=dat_match.system_name or "DAT",
        )]
        return RomAnalysisResult(
            file_path=file_path,
            file_name=os.path.basename(file_path),
            file_size=os.path.getsize(file_path),
            file_ext=os.path.splitext(file_path)[1].lower(),
            system_id=platform["id"],
            system_name=platform["name"],
            system_type=platform["type"],
            platform_slug=platform["slug"],
            libretro_system=platform["libretro"],
            is_arcade=False,
            is_disc=False,
            is_playable=True,
            confidence="high" if confidence_score >= 0.90 else "medium",
            confidence_score=confidence_score,
            header_metadata=HeaderMetadata(title=dat_match.title or dat_match.rom_name or os.path.splitext(os.path.basename(file_path))[0]),
            evidence=evidence,
            detection_methods=["dat_crc"],
            alternatives=[
                {
                    "rom_name": c.rom_name,
                    "title": c.title,
                    "platform": c.platform,
                    "source": c.system_name,
                    "score": c.score,
                }
                for c in dat_match.candidates[1:5]
            ],
            summary=f"콘솔 DAT CRC 아카이브: {platform['name']} ({dat_match.title or dat_match.rom_name})",
        )

    @classmethod
    def _apply_console_dat(cls, file_path: str, result: RomAnalysisResult):
        ext = os.path.splitext(file_path)[1].lower()
        dat_match = None
        if ext in {".zip", ".7z"}:
            dat_match = DatMatcher.match_archive(file_path)
        elif ext in _CONSOLE_DAT_EXTENSIONS and os.path.getsize(file_path) <= 128 * 1024 * 1024:
            dat_match = DatMatcher.match_file(file_path)

        if not dat_match or not dat_match.matched or not dat_match.platform:
            return
        if dat_match.platform.lower() == "arcade":
            return

        platform = _DAT_PLATFORM_MAP.get(dat_match.platform)
        if not platform:
            return

        dat_conf = dat_match.confidence_score
        current_conf = max((e.confidence for e in result.evidence), default=result.confidence_score)
        compatible = EvidenceScorer._systems_compatible(result.system_id, platform["id"])

        if not compatible:
            weak_existing = bool(result.evidence) and all(
                e.method in EvidenceScorer.WEAK_METHODS for e in result.evidence
            )
            # 이름만 같은 DAT 항목은 다른 기종으로 교정할 근거가 아니다.
            has_crc_evidence = dat_match.matched_count > 0
            if has_crc_evidence and dat_match.status != "ambiguous" and (
                current_conf < 0.85 or dat_conf >= current_conf + 0.05 or weak_existing
            ):
                previous = result.system_id
                result.system_id = platform["id"]
                result.system_name = platform["name"]
                result.system_type = platform["type"]
                result.platform_slug = platform["slug"]
                result.libretro_system = platform["libretro"]
                result.add_warning(
                    f"약한 기존 판별({previous})을 DAT CRC 근거로 {result.system_id} 기종으로 교정했습니다."
                )
            elif has_crc_evidence:
                conflict = (
                    f"바이너리 판별은 {result.system_id}, DAT CRC({dat_match.system_name})는 "
                    f"{platform['id']} 기종을 가리킵니다."
                )
                if conflict not in result.conflicts:
                    result.conflicts.append(conflict)
                result.add_warning(conflict)
            else:
                # name-only 후보는 참고 정보만 유지하고 기종 판별에는 사용하지 않는다.
                return

        method = "dat_crc" if dat_match.matched_count else "dat_name"
        result.evidence.append(DetectionEvidence(
            method=method,
            confidence=dat_conf,
            detail=(
                f"DAT {dat_match.status} match: {dat_match.title or dat_match.rom_name}; "
                f"coverage {dat_match.match_rate:.2f}%/{dat_match.archive_match_rate:.2f}%"
            ),
            source=dat_match.system_name or "DAT",
        ))
        if method not in result.detection_methods:
            result.detection_methods.append(method)

        if dat_match.status in {"exact", "strong"} and dat_match.title:
            result.header_metadata.title = dat_match.title

        result.alternatives = [
            {
                "rom_name": c.rom_name, "title": c.title, "platform": c.platform,
                "source": c.system_name, "score": c.score,
            }
            for c in dat_match.candidates[1:5]
        ]
        if dat_match.status == "ambiguous":
            result.identity_status = "ambiguous"

    @classmethod
    def _ensure_evidence(cls, result: RomAnalysisResult):
        """Compatibility fallback only; detectors are expected to emit their own evidence."""
        if result.evidence:
            if not result.detection_methods:
                result.detection_methods = list(dict.fromkeys(e.method for e in result.evidence))
            return

        # Do not infer a binary signature from confidence/header fields after the fact.
        # A detector that has not yet been migrated is explicitly marked as legacy.
        result.evidence.append(DetectionEvidence(
            method="legacy_detector_fallback",
            confidence=result.confidence_score,
            detail=result.summary or result.system_name,
            source="rom_analyzer",
        ))
        result.detection_methods = ["legacy_detector_fallback"]

    @classmethod
    def _calc_hashes(cls, file_path: str, result: RomAnalysisResult):
        """파일의 CRC32, MD5, SHA1 해시값 계산"""
        try:
            crc = 0
            md5_obj = hashlib.md5()
            sha1_obj = hashlib.sha1()

            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    crc = zlib.crc32(chunk, crc)
                    md5_obj.update(chunk)
                    sha1_obj.update(chunk)

            result.crc32 = f"{crc & 0xFFFFFFFF:08x}"
            result.md5 = md5_obj.hexdigest()
            result.sha1 = sha1_obj.hexdigest()
        except Exception as e:
            result.add_warning(f"해시 계산 실패: {e}")


def analyze(file_path: Union[str, Path], compute_hashes: bool = False) -> RomAnalysisResult:
    """ROM 분석 기본 진입 함수"""
    return RomAnalyzer.analyze(file_path, compute_hashes=compute_hashes)


# 유저 요청 별칭 함수 (모듈.analyzer(패스) 지원)
def analyzer(file_path: Union[str, Path], compute_hashes: bool = False) -> RomAnalysisResult:
    """ROM 분석 별칭 함수 (analyzer)"""
    return RomAnalyzer.analyze(file_path, compute_hashes=compute_hashes)
