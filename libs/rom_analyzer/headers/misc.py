# -*- coding: utf-8 -*-
"""
기타 콘솔 및 핸드헬드 카트리지 ROM 바이너리 헤더 분석기.
PC Engine (TurboGrafx-16), SuperGrafx, Neo Geo Pocket, WonderSwan, Atari 2600/7800/Lynx 등.
"""

import os
from typing import Optional

from ..models import RomAnalysisResult, HeaderMetadata, DetectionEvidence


def _evidence_kwargs(method: str, confidence: float, detail: str, binary: bool = False):
    methods = [method]
    if binary:
        methods.append("binary_header")
    return {
        "confidence_score": confidence,
        "evidence": [DetectionEvidence(method=method, confidence=confidence, detail=detail, source="misc_header")],
        "detection_methods": methods,
    }


class MiscHeaderDetector:
    """기타 레트로 기종 헤더 분석"""

    @classmethod
    def detect(cls, file_path: str, data: bytes, signature_only: bool = False) -> Optional[RomAnalysisResult]:
        ext = os.path.splitext(file_path)[1].lower()
        size = len(data)
        base_name = os.path.basename(file_path)
        stem = os.path.splitext(base_name)[0]

        if signature_only:
            if data.startswith(b"ATARI7800"):
                ext = ".a78"
            elif data.startswith(b"LYNX"):
                ext = ".lnx"
            else:
                return None

        # 1. PC Engine / TurboGrafx-16 / SuperGrafx
        if ext in [".pce", ".sgx"]:
            is_sgx = ext == ".sgx"
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=size,
                file_ext=ext,
                system_id="supergrafx" if is_sgx else "pce",
                system_name="NEC PC Engine SuperGrafx" if is_sgx else "NEC PC Engine / TurboGrafx-16",
                system_type="console",
                platform_slug="supergrafx" if is_sgx else "pce",
                libretro_system="NEC_-_PC_Engine_SuperGrafx" if is_sgx else "NEC_-_PC_Engine_-_TurboGrafx_16",
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="high",
                header_metadata=HeaderMetadata(title=stem),
                **_evidence_kwargs("extension_hint", 0.60, f"identified from {ext} extension; no strong HuCard signature validated"),
                summary=f"NEC PC엔진 {'슈퍼그래픽스' if is_sgx else 'HuCard'} 롬"
            )

        # 2. Neo Geo Pocket / Neo Geo Pocket Color
        if ext in [".ngp", ".ngc", ".ngpc", ".npc"]:
            is_color = ext in [".ngc", ".ngpc"] or (size >= 0x24 and data[0x23] == 0x10)
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=size,
                file_ext=ext,
                system_id="ngpc" if is_color else "ngp",
                system_name="SNK Neo Geo Pocket Color" if is_color else "SNK Neo Geo Pocket",
                system_type="handheld",
                platform_slug="ngpc" if is_color else "ngp",
                libretro_system="SNK_-_Neo_Geo_Pocket_Color" if is_color else "SNK_-_Neo_Geo_Pocket",
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="medium",
                header_metadata=HeaderMetadata(title=stem),
                **_evidence_kwargs("extension_hint", 0.60, f"identified from {ext} extension; color flag is not used as system identity evidence"),
                summary=f"SNK 네오지오 포켓 {'컬러' if is_color else ''} 카트리지 롬"
            )

        # 3. Bandai WonderSwan / WonderSwan Color
        if ext in [".ws", ".wsc"]:
            is_color = ext == ".wsc"
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=size,
                file_ext=ext,
                system_id="wsc" if is_color else "wonderswan",
                system_name="Bandai WonderSwan Color" if is_color else "Bandai WonderSwan",
                system_type="handheld",
                platform_slug="wsc" if is_color else "wonderswan",
                libretro_system="Bandai_-_WonderSwan_Color" if is_color else "Bandai_-_WonderSwan",
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="high",
                header_metadata=HeaderMetadata(title=stem),
                **_evidence_kwargs("extension_hint", 0.60, f"identified from {ext} extension only"),
                summary=f"반다이 원더스완 {'컬러' if is_color else ''} 롬"
            )

        # 4. Atari 2600 / 7800 / Lynx
        if ext == ".a26":
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=size,
                file_ext=ext,
                system_id="atari2600",
                system_name="Atari 2600",
                system_type="console",
                platform_slug="atari2600",
                libretro_system="Atari_-_2600",
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="high",
                header_metadata=HeaderMetadata(title=stem),
                **_evidence_kwargs("extension_hint", 0.60, "identified from .a26 extension only"),
                summary="아타리 2600 카트리지 롬"
            )

        if ext == ".a78" or data.startswith(b"ATARI7800"):
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=size,
                file_ext=ext,
                system_id="atari7800",
                system_name="Atari 7800",
                system_type="console",
                platform_slug="atari7800",
                libretro_system="Atari_-_7800",
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="high",
                header_metadata=HeaderMetadata(title=stem, header_type="Atari 7800 Header"),
                **_evidence_kwargs("atari7800_signature" if data.startswith(b"ATARI7800") else "extension_hint", 0.98 if data.startswith(b"ATARI7800") else 0.60, "ATARI7800 header signature matched" if data.startswith(b"ATARI7800") else "identified from .a78 extension only", binary=data.startswith(b"ATARI7800")),
                summary="아타리 7800 카트리지 롬"
            )

        if ext == ".lnx" or data.startswith(b"LYNX"):
            return RomAnalysisResult(
                file_path=file_path,
                file_name=base_name,
                file_size=size,
                file_ext=ext,
                system_id="lynx",
                system_name="Atari Lynx",
                system_type="handheld",
                platform_slug="lynx",
                libretro_system="Atari_-_Lynx",
                is_arcade=False,
                is_disc=False,
                is_playable=True,
                confidence="high",
                header_metadata=HeaderMetadata(title=stem, header_type="Atari Lynx Header"),
                **_evidence_kwargs("lynx_signature" if data.startswith(b"LYNX") else "extension_hint", 0.98 if data.startswith(b"LYNX") else 0.60, "LYNX header signature matched" if data.startswith(b"LYNX") else "identified from .lnx extension only", binary=data.startswith(b"LYNX")),
                summary="아타리 링스 (Lynx) 핸드헬드 롬"
            )

        return None
