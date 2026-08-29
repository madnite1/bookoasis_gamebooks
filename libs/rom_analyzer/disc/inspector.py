# -*- coding: utf-8 -*-
"""
디스크 기반 미디어(CD/DVD/GD-ROM) 구조 검사기.
CUE, GDI, CHD, ISO, BIN, PBP, CCD, MDS 포맷 파싱 및 누락 트랙/보조 파일 탐지.
"""

import os
import logging
import re
from typing import Optional, List, Dict, Any, Tuple

from ..models import RomAnalysisResult, DiscInfo, DiscEntryInfo, BiosInfo, HeaderMetadata, DetectionEvidence
from ..evidence import EvidenceScorer
from ..db import query_disc_serial, query_bios_manifest
from ..arcade.bios_db import CONSOLE_BIOS_CATALOG
from .serial_scanner import DiscSerialScanner
from .parsers import parse_cue, parse_gdi, parse_chd, parse_m3u, parse_pbp, parse_param_sfo
from .parsers.common import find_case_insensitive

logger = logging.getLogger(__name__)

def _disc_evidence_kwargs(header_data: Dict[str, Any], confidence_score: float, hint_reason: Optional[str] = None,
                          structural_method: Optional[str] = None, structural_detail: Optional[str] = None,
                          structural_confidence: Optional[float] = None):
    evidence: List[DetectionEvidence] = []
    methods: List[str] = []

    if header_data.get("_serial_db_match"):
        evidence.append(DetectionEvidence(
            method="serial_db", confidence=0.99,
            detail=f"disc serial matched metadata DB: {header_data.get('serial')}",
            source="rom_metadata.db",
        ))
        methods.append("serial_db")

    if header_data.get("system_id") and header_data.get("header_type"):
        sig_conf = max(0.95, min(0.98, confidence_score))
        evidence.append(DetectionEvidence(
            method="disc_signature", confidence=sig_conf,
            detail=f"{header_data.get('header_type')} binary signature/header matched",
            source="disc_serial_scanner",
        ))
        methods.extend(["disc_signature", "binary_header"])
    elif hint_reason:
        method = "filename_hint" if "파일명 태그" in hint_reason else "path_hint"
        evidence.append(DetectionEvidence(
            method=method, confidence=confidence_score, detail=hint_reason, source="disc_hint",
        ))
        methods.append(method)
    elif structural_method:
        ev_conf = structural_confidence if structural_confidence is not None else confidence_score
        evidence.append(DetectionEvidence(
            method=structural_method, confidence=ev_conf,
            detail=structural_detail or structural_method, source="disc_inspector",
        ))
        methods.append(structural_method)
    else:
        evidence.append(DetectionEvidence(
            method="disc_format_only", confidence=confidence_score,
            detail="disc container/extension recognized but system signature was not found",
            source="disc_inspector",
        ))
        methods.append("disc_format_only")

    return {"evidence": evidence, "detection_methods": list(dict.fromkeys(methods))}



class DiscInspector:
    """디스크 미디어 검사기"""

    DISC_EXTENSIONS = {
        ".cue", ".gdi", ".chd", ".m3u", ".iso", ".bin", ".img",
        ".pbp", ".ccd", ".mds", ".mdf", ".rvz", ".wbfs", ".cso"
    }

    @classmethod
    def is_disc_file(cls, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        return ext in cls.DISC_EXTENSIONS

    @classmethod
    def detect(cls, file_path: str) -> Optional[RomAnalysisResult]:
        """디스크 파일 분석 및 RomAnalysisResult 반환"""
        if not os.path.exists(file_path):
            return None

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in cls.DISC_EXTENSIONS:
            return None

        base_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        base_dir = os.path.dirname(os.path.abspath(file_path))

        # 1. CUE 시트 분석
        if ext == ".cue":
            return cls._analyze_cue(file_path, base_name, file_size, base_dir)

        # 2. GDI 시트 분석 (Dreamcast)
        if ext == ".gdi":
            return cls._analyze_gdi(file_path, base_name, file_size, base_dir)

        # 3. M3U 멀티디스크 플레이리스트 분석
        if ext == ".m3u":
            return cls._analyze_m3u(file_path, base_name, file_size, base_dir)

        # 4. CHD 압축 디스크 이미지 분석
        if ext == ".chd":
            return cls._analyze_chd(file_path, base_name, file_size)

        # 5. PBP (PlayStation EBOOT) 분석
        if ext == ".pbp":
            return cls._analyze_pbp(file_path, base_name, file_size)

        # 5. CCD / MDS 메타데이터 시트 분석
        if ext in [".ccd", ".mds"]:
            return cls._analyze_sheet(file_path, base_name, file_size, base_dir, ext)

        # 6. Standalone ISO / BIN / IMG 분석
        if ext in [".iso", ".bin", ".img", ".rvz", ".wbfs", ".cso"]:
            return cls._analyze_raw_disc(file_path, base_name, file_size, ext)

        return None

    @classmethod
    def _analyze_m3u(cls, file_path: str, base_name: str, file_size: int, base_dir: str) -> RomAnalysisResult:
        """M3U를 멀티디스크 게임의 대표 실행 파일로 분석한다."""
        parsed = parse_m3u(file_path, base_dir)
        warnings: List[str] = []
        if parsed.error:
            warnings.append(parsed.error)
        if parsed.missing_files:
            warnings.append(f"M3U 참조 파일 누락 {len(parsed.missing_files)}개: {', '.join(parsed.missing_files[:3])}")
        if parsed.invalid_references:
            warnings.append(
                f"M3U에 허용되지 않은 참조 {len(parsed.invalid_references)}개: "
                f"{', '.join(parsed.invalid_references[:3])}"
            )
        if not parsed.referenced_files:
            warnings.append("M3U 플레이리스트에 유효한 디스크 엔트리가 없습니다.")

        child_results = []
        child_by_ref = {}
        bundle_refs = list(parsed.referenced_files)
        bundle_missing = list(parsed.missing_files)
        seen_refs = {r.casefold() for r in bundle_refs}

        for rel_ref, resolved in zip(parsed.referenced_files, parsed.resolved_files):
            if rel_ref in parsed.missing_files or not os.path.isfile(resolved):
                continue
            child = cls.detect(resolved)
            if child is None:
                warnings.append(f"M3U 참조 디스크를 분석할 수 없습니다: {rel_ref}")
                continue
            EvidenceScorer.apply(child)
            child_results.append((rel_ref, resolved, child))
            child_by_ref[rel_ref] = child

            # CUE/CCD/GDI처럼 자식 디스크가 다시 sidecar를 참조하는 경우 번들에 함께 포함한다.
            if child.disc_info.is_multi_file:
                child_dir = os.path.dirname(os.path.abspath(resolved))
                for nested in child.disc_info.referenced_files:
                    nested_abs = os.path.abspath(os.path.join(child_dir, nested))
                    try:
                        inside = os.path.commonpath([base_dir, nested_abs]) == base_dir
                    except ValueError:
                        inside = False
                    if not inside:
                        warnings.append(f"M3U 하위 sidecar가 기준 폴더를 벗어납니다: {nested}")
                        continue
                    nested_rel = os.path.relpath(nested_abs, base_dir).replace(os.sep, "/")
                    if nested_rel.casefold() not in seen_refs:
                        seen_refs.add(nested_rel.casefold())
                        bundle_refs.append(nested_rel)
                    if nested in child.disc_info.missing_files and nested_rel not in bundle_missing:
                        bundle_missing.append(nested_rel)

        disc_entries: List[DiscEntryInfo] = []
        for rel_ref in parsed.referenced_files:
            child = child_by_ref.get(rel_ref)
            if child is None:
                disc_entries.append(DiscEntryInfo(
                    path=rel_ref,
                    disc_format=os.path.splitext(rel_ref)[1].lstrip(".").upper() or None,
                    is_complete=False if rel_ref in parsed.missing_files else True,
                    is_playable=False,
                ))
                continue
            disc_entries.append(DiscEntryInfo(
                path=rel_ref,
                disc_format=child.disc_info.disc_format,
                system_id=child.system_id,
                system_name=child.system_name,
                title=child.header_metadata.title,
                serial=child.header_metadata.serial,
                region=child.header_metadata.region,
                confidence_score=child.confidence_score,
                identity_status=child.identity_status,
                is_complete=child.disc_info.is_complete,
                is_playable=child.is_playable,
                detection_methods=list(child.detection_methods),
            ))

        known = [(rel, child) for rel, _, child in child_results if child.system_id != "unknown"]
        known_systems = {child.system_id for _, child in known}
        unknown_children = [(rel, child) for rel, _, child in child_results if child.system_id == "unknown"]
        conflicts: List[str] = []
        hint_reason = None
        source_child = None
        identity_status = "unknown"

        if len(known_systems) == 1:
            system_id = next(iter(known_systems))
            source_child = max((child for _, child in known), key=lambda r: r.confidence_score)
            system_name = source_child.system_name
            confidence_score = min(0.99, source_child.confidence_score)
            if unknown_children:
                confidence_score = min(confidence_score, 0.80)
                warnings.append(
                    f"M3U의 {len(unknown_children)}개 참조 디스크는 기종을 직접 식별하지 못해 "
                    f"식별된 디스크의 {system_id} 판정을 세트에 적용했습니다."
                )
        elif len(known_systems) > 1:
            systems = ", ".join(sorted(known_systems))
            system_id = "unknown"
            system_name = "Conflicting M3U Multi-Disc Set"
            confidence_score = 0.25
            identity_status = "ambiguous"
            conflict = f"M3U 참조 디스크들의 기종 판별이 서로 다릅니다: {systems}"
            conflicts.append(conflict)
            warnings.append(conflict)
        else:
            hint_res = cls._infer_system_from_hints(file_path)
            if hint_res:
                system_id, system_name, hint_reason = hint_res
                confidence_score = 0.65
                warnings.append(f"참조 디스크에서 기종 시그니처를 찾지 못해 {hint_reason}를 사용했습니다.")
            else:
                system_id = "unknown"
                system_name = "Unknown M3U Multi-Disc Set"
                confidence_score = 0.40 if parsed.referenced_files else 0.25
                warnings.append("M3U와 참조 디스크에서 기종 식별 근거를 찾지 못했습니다.")

        is_complete = bool(parsed.referenced_files) and not bundle_missing and not parsed.invalid_references and not parsed.error
        if any(not child.disc_info.is_complete for _, _, child in child_results):
            is_complete = False
            warnings.append("M3U가 참조하는 디스크 세트 중 불완전한 항목이 있습니다.")
        is_playable = is_complete and system_id != "unknown" and not conflicts

        evidence = [DetectionEvidence(
            method="m3u_playlist",
            confidence=min(confidence_score, 0.50),
            detail=f"M3U playlist parsed with {len(parsed.referenced_files)} disc entrie(s)",
            source="m3u_parser",
        )]
        methods = ["m3u_playlist"]
        if source_child:
            child_evidence = source_child.primary_evidence
            if child_evidence is None and source_child.evidence:
                child_evidence = max(source_child.evidence, key=lambda e: e.confidence)
            if child_evidence:
                evidence.append(DetectionEvidence(
                    method="m3u_child_system",
                    confidence=confidence_score,
                    detail=(
                        f"system inherited from referenced disc {source_child.file_name}: "
                        f"{child_evidence.method}"
                    ),
                    source=child_evidence.source or "disc_inspector",
                ))
                methods.append("m3u_child_system")
        if hint_reason:
            hint_method = "filename_hint" if "파일명 태그" in hint_reason else "path_hint"
            evidence.append(DetectionEvidence(
                method=hint_method, confidence=0.65, detail=hint_reason, source="disc_hint"
            ))
            methods.append(hint_method)

        companion_exts = [".m3u"]
        for ref in bundle_refs:
            ext = os.path.splitext(ref)[1].lower()
            if ext and ext not in companion_exts:
                companion_exts.append(ext)

        serials = sorted({entry.serial for entry in disc_entries if entry.serial})
        regions = sorted({entry.region for entry in disc_entries if entry.region})
        disc_formats = sorted({entry.disc_format for entry in disc_entries if entry.disc_format})
        child_system_list = sorted({entry.system_id for entry in disc_entries if entry.system_id != "unknown"})

        if source_child:
            system_type = source_child.system_type
        elif system_id == "psp":
            system_type = "handheld"
        else:
            system_type = "console" if system_id != "unknown" else "unknown"

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            file_ext=".m3u",
            system_id=system_id,
            system_name=system_name,
            system_type=system_type,
            platform_slug=cls._get_platform_slug(system_id),
            libretro_system=cls._get_libretro_system(system_id),
            is_arcade=False,
            is_disc=True,
            is_playable=is_playable,
            confidence="high" if confidence_score >= 0.90 else "medium" if confidence_score >= 0.55 else "low",
            confidence_score=confidence_score,
            disc_info=DiscInfo(
                is_disc=True,
                disc_format="M3U",
                is_multi_file=True,
                is_complete=is_complete,
                referenced_files=bundle_refs,
                missing_files=bundle_missing,
                track_count=len(parsed.referenced_files),
                companion_extensions=companion_exts,
                playlist_entries=list(parsed.referenced_files),
                disc_count=len(parsed.referenced_files),
                disc_entries=disc_entries,
                container_metadata={
                    "disc_formats": disc_formats,
                    "systems": child_system_list,
                    "serials": serials,
                },
            ),
            bios_info=cls._make_bios_info(system_id),
            header_metadata=HeaderMetadata(
                title=os.path.splitext(base_name)[0],
                serial=serials[0] if len(serials) == 1 else None,
                region=regions[0] if len(regions) == 1 else None,
                header_type="M3U Multi-Disc Playlist",
            ),
            evidence=evidence,
            detection_methods=methods,
            identity_status=identity_status,
            conflicts=conflicts,
            summary=(
                f"M3U 멀티디스크 세트 ({system_name}) | 디스크 {len(parsed.referenced_files)}개 | "
                f"{'완전함' if is_complete else '누락/오류 있음'}"
            ),
            warnings=warnings,
        )

    @classmethod
    def _analyze_cue(cls, file_path: str, base_name: str, file_size: int, base_dir: str) -> RomAnalysisResult:
        """CUE 시트 및 참조 트랙 검사"""
        parsed = parse_cue(file_path, base_dir)
        referenced_files = parsed.referenced_files
        missing_files = parsed.missing_files
        first_data_file = parsed.first_data_file
        parse_error = parsed.error

        # 첫 번째 데이터 트랙 또는 CUE 디렉터리 내 BIN 파일 헤더 정밀 스캔
        header_data = cls._scan_first_track(first_data_file or file_path)
        cls._enrich_from_serial(header_data)

        warnings = []
        if parse_error:
            warnings.append(parse_error)
        if missing_files:
            warnings.append(f"누락된 트랙 파일 {len(missing_files)}개: {', '.join(missing_files[:3])}")

        system_id = header_data.get("system_id")
        confidence = "high"
        confidence_score = 0.95
        hint_reason = None

        if system_id:
            system_name = header_data.get("system_name") or f"Disc-Based Console ({system_id.upper()})"
        else:
            hint_res = cls._infer_system_from_hints(file_path)
            if hint_res:
                system_id, system_name, hint_reason = hint_res
                confidence = "medium"
                confidence_score = 0.65
                warnings.append(f"바이너리 시그니처 미검출; {hint_reason}를 기반으로 기종을 추정했습니다.")
            else:
                system_id = "unknown"
                system_name = "Unknown Optical Disc Image (CUE/BIN)"
                confidence = "low"
                confidence_score = 0.25
                warnings.append("CUE/BIN 트랙에서 기종 식별 시그니처를 찾지 못했습니다. 수동 지정이 필요합니다.")

        libretro_sys = cls._get_libretro_system(system_id)
        platform_slug = cls._get_platform_slug(system_id)

        is_complete = len(missing_files) == 0
        track_count = len(referenced_files)
        is_multi = track_count > 1 or (track_count == 1 and not referenced_files[0].lower().endswith(".cue"))

        disc_info = DiscInfo(
            is_disc=True,
            disc_format="CUE/BIN",
            is_multi_file=is_multi,
            is_complete=is_complete,
            referenced_files=referenced_files,
            missing_files=missing_files,
            track_count=track_count,
            companion_extensions=[".cue", ".bin", ".wav", ".iso"]
        )

        bios_info = cls._make_bios_info(system_id)
        header_meta = HeaderMetadata(
            title=header_data.get("title") or os.path.splitext(base_name)[0],
            serial=header_data.get("serial"),
            region=header_data.get("region"),
            header_type=header_data.get("header_type")
        )

        summary_parts = [f"디스크 CUE 시트 ({system_name})", f"트랙 {track_count}개"]
        if not is_complete:
            summary_parts.append(f"경고: {len(missing_files)}개 트랙 누락됨")
        else:
            summary_parts.append("모든 트랙 파일 정상 확인")

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            file_ext=".cue",
            system_id=system_id,
            system_name=system_name,
            system_type="console" if system_id != "unknown" else "unknown",
            platform_slug=platform_slug,
            libretro_system=libretro_sys,
            is_arcade=False,
            is_disc=True,
            is_playable=is_complete,
            confidence=confidence,
            confidence_score=confidence_score,
            disc_info=disc_info,
            bios_info=bios_info,
            header_metadata=header_meta,
            **_disc_evidence_kwargs(header_data, confidence_score, hint_reason=hint_reason),
            summary=" | ".join(summary_parts),
            warnings=warnings
        )

    @classmethod
    def _analyze_gdi(cls, file_path: str, base_name: str, file_size: int, base_dir: str) -> RomAnalysisResult:
        """Dreamcast GDI 시트 및 트랙 파일 검사"""
        parsed = parse_gdi(file_path, base_dir)
        referenced_files = parsed.referenced_files
        missing_files = parsed.missing_files
        first_track_path = parsed.first_track_path
        parse_error = parsed.error

        header_data = cls._scan_first_track(first_track_path or file_path)
        cls._enrich_from_serial(header_data)
        is_complete = len(missing_files) == 0

        disc_info = DiscInfo(
            is_disc=True,
            disc_format="GDI",
            is_multi_file=True,
            is_complete=is_complete,
            referenced_files=referenced_files,
            missing_files=missing_files,
            track_count=len(referenced_files),
            companion_extensions=[".gdi", ".bin", ".raw"]
        )

        bios_info = cls._make_bios_info("dreamcast")
        header_meta = HeaderMetadata(
            title=header_data.get("title") or os.path.splitext(base_name)[0],
            serial=header_data.get("serial"),
            region=header_data.get("region"),
            header_type=header_data.get("header_type") or "Dreamcast IP.BIN"
        )
        warnings = []
        if parse_error:
            warnings.append(parse_error)
        if missing_files:
            warnings.append(f"누락된 트랙: {', '.join(missing_files)}")

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            file_ext=".gdi",
            system_id="dreamcast",
            system_name="Sega Dreamcast (GD-ROM)",
            system_type="console",
            platform_slug="dreamcast",
            libretro_system="Sega_-_Dreamcast",
            is_arcade=False,
            is_disc=True,
            is_playable=is_complete,
            confidence="high",
            disc_info=disc_info,
            bios_info=bios_info,
            header_metadata=header_meta,
            **_disc_evidence_kwargs(
                header_data, 0.95,
                structural_method="gdi_structure",
                structural_detail=f"GDI descriptor parsed with {len(referenced_files)} referenced track(s)",
                structural_confidence=0.95 if referenced_files else 0.75,
            ),
            summary=f"드림캐스트 GDI 디스크 세트 (트랙 {len(referenced_files)}개, {'완전함' if is_complete else '일부 트랙 누락'})",
            warnings=warnings
        )

    @classmethod
    def _analyze_chd(cls, file_path: str, base_name: str, file_size: int) -> RomAnalysisResult:
        """CHD 단일 압축 디스크 이미지 분석 및 최소 헤더 무결성 검사."""
        parsed = parse_chd(file_path)
        header_data = parsed.header_data
        warnings = [parsed.error] if parsed.error else []
        warnings.extend(parsed.warnings)
        hint_reason = None

        if parsed.header_valid:
            cls._enrich_from_serial(header_data)
            system_id = header_data.get("system_id")
            if system_id:
                system_name = header_data.get("system_name") or f"CHD Disc Image ({system_id.upper()})"
                confidence_score = 0.95
            else:
                hint_res = cls._infer_system_from_hints(file_path)
                if hint_res:
                    system_id, system_name, hint_reason = hint_res
                    confidence_score = 0.65
                    warnings.append(f"CHD 내부 시그니처 미검출; {hint_reason}를 기반으로 기종을 추정했습니다.")
                else:
                    system_id = "unknown"
                    system_name = "Unknown CHD Disc Image"
                    confidence_score = 0.40
                    warnings.append("유효한 CHD 헤더는 확인했지만 내부에서 기종 식별 근거를 찾지 못했습니다.")
        else:
            system_id = "unknown"
            system_name = "Malformed CHD Disc Image"
            confidence_score = 0.20
            warnings.append("CHD 헤더 검증에 실패하여 실행 가능한 디스크 이미지로 취급하지 않습니다.")

        confidence = "high" if confidence_score >= 0.90 else "medium" if confidence_score >= 0.55 else "low"
        platform_slug = cls._get_platform_slug(system_id)
        libretro_sys = cls._get_libretro_system(system_id)

        disc_info = DiscInfo(
            is_disc=True,
            disc_format="CHD",
            is_multi_file=False,
            is_complete=parsed.header_valid,
            referenced_files=[base_name],
            missing_files=[],
            track_count=len(parsed.tracks) or 1,
            container_metadata={
                "version": parsed.version,
                "header_length": parsed.header_length,
                "logical_bytes": parsed.logical_bytes,
                "map_offset": parsed.map_offset,
                "metadata_offset": parsed.metadata_offset,
                "hunk_bytes": parsed.hunk_bytes,
                "unit_bytes": parsed.unit_bytes,
                "compressors": list(parsed.compressors),
                "metadata_tags": [entry.tag for entry in parsed.metadata_entries],
                "metadata_text": [entry.text for entry in parsed.metadata_entries if entry.text],
                "tracks": list(parsed.tracks),
                "optical_media": parsed.optical_media,
                "content_scan_mode": "container-metadata-only",
            },
        )

        if parsed.header_valid:
            structural_method = "chd_header"
            media_detail = f", {parsed.optical_media} metadata" if parsed.optical_media else ""
            structural_detail = (
                f"CHD v{parsed.version} header validated ({parsed.header_length} bytes{media_detail})"
            )
            structural_conf = 0.40 if system_id == "unknown" else confidence_score
        else:
            structural_method = "malformed_chd"
            structural_detail = parsed.error or "CHD header validation failed"
            structural_conf = 0.20

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            file_ext=".chd",
            system_id=system_id,
            system_name=system_name,
            system_type="console" if system_id != "unknown" else "unknown",
            platform_slug=platform_slug,
            libretro_system=libretro_sys,
            is_arcade=False,
            is_disc=True,
            is_playable=parsed.header_valid,
            confidence=confidence,
            confidence_score=confidence_score,
            disc_info=disc_info,
            bios_info=cls._make_bios_info(system_id),
            header_metadata=HeaderMetadata(
                title=header_data.get("title") or os.path.splitext(base_name)[0],
                serial=header_data.get("serial"),
                region=header_data.get("region"),
                header_type=header_data.get("header_type") or (f"CHD v{parsed.version}" if parsed.header_valid else None)
            ),
            **_disc_evidence_kwargs(
                header_data, confidence_score, hint_reason=hint_reason,
                structural_method=structural_method,
                structural_detail=structural_detail,
                structural_confidence=structural_conf,
            ),
            summary=(
                f"단일 압축 디스크 이미지 (CHD v{parsed.version}{', ' + parsed.optical_media if parsed.optical_media else ''}): {system_name}"
                if parsed.header_valid else f"손상되었거나 잘못된 CHD 이미지: {base_name}"
            ),
            warnings=warnings
        )

    @classmethod
    def _analyze_pbp(cls, file_path: str, base_name: str, file_size: int) -> RomAnalysisResult:
        """PlayStation EBOOT.PBP / PSP PBP 포맷 분석"""
        parsed = parse_pbp(file_path, file_size=file_size, default_title=os.path.splitext(base_name)[0])
        pbp_header_valid = parsed.header_valid
        system_id = parsed.system_id
        system_name = parsed.system_name
        title = parsed.title
        serial = parsed.serial
        parse_error = parsed.error
        result_confidence = "high" if pbp_header_valid else "low"
        result_confidence_score = 0.98 if pbp_header_valid else 0.25
        header_data: Dict[str, Any] = {}
        if pbp_header_valid:
            header_data = {
                "system_id": system_id,
                "system_name": system_name,
                "serial": serial,
                "header_type": "PBP PARAM.SFO",
            }
            if serial:
                cls._enrich_from_serial(header_data)
                system_id = header_data.get("system_id") or system_id

        disc_info = DiscInfo(
            is_disc=True,
            disc_format="PBP",
            is_multi_file=False,
            is_complete=pbp_header_valid,
            referenced_files=[base_name],
            missing_files=[],
            track_count=1
        )

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            file_ext=".pbp",
            system_id=system_id,
            system_name=system_name,
            system_type=("console" if system_id == "psx" else "handheld" if system_id == "psp" else "unknown"),
            platform_slug=system_id,
            libretro_system=cls._get_libretro_system(system_id),
            is_arcade=False,
            is_disc=True,
            is_playable=pbp_header_valid,
            confidence=result_confidence,
            confidence_score=result_confidence_score,
            disc_info=disc_info,
            bios_info=cls._make_bios_info(system_id),
            header_metadata=HeaderMetadata(
                title=title,
                serial=serial if pbp_header_valid else None,
                header_type="PBP PARAM.SFO" if pbp_header_valid else None,
            ),
            **_disc_evidence_kwargs(
                header_data if pbp_header_valid else {}, result_confidence_score,
                structural_method="pbp_header" if pbp_header_valid else "malformed_pbp",
                structural_detail=(
                    "PBP magic and section offset table validated"
                    if pbp_header_valid else "PBP header validation failed; package is truncated or malformed"
                ),
                structural_confidence=result_confidence_score,
            ),
            summary=f"{system_name} 단일 통합 패키지 (PBP)",
            warnings=[parse_error] if parse_error else []
        )

    @classmethod
    def _analyze_sheet(cls, file_path: str, base_name: str, file_size: int, base_dir: str, ext: str) -> RomAnalysisResult:
        """CCD / MDS 디스크 시트 분석"""
        companion_ext = ".img" if ext == ".ccd" else ".mdf"
        stem = os.path.splitext(base_name)[0]
        expected_companion = stem + companion_ext
        companion_path = os.path.join(base_dir, expected_companion)

        exists = os.path.exists(companion_path)
        if not exists:
            found = cls._find_case_insensitive(base_dir, expected_companion)
            if found:
                exists = True
                companion_path = found

        missing = [] if exists else [expected_companion]
        header_data = cls._scan_first_track(companion_path if exists else file_path)
        cls._enrich_from_serial(header_data)

        system_id = header_data.get("system_id")
        confidence = "high" if exists else "low"
        confidence_score = 0.95 if (exists and system_id) else 0.50
        warnings = [f"필수 보조 파일 누락: {expected_companion}"] if not exists else []
        hint_reason = None

        if system_id:
            system_name = header_data.get("system_name") or f"Disc Image ({ext.upper()})"
        else:
            hint_res = cls._infer_system_from_hints(file_path)
            if hint_res:
                system_id, system_name, hint_reason = hint_res
                confidence = "medium" if exists else "low"
                confidence_score = 0.65 if exists else 0.25
                warnings.append(f"바이너리 시그니처 미검출; {hint_reason}를 기반으로 기종을 추정했습니다.")
            else:
                system_id = "unknown"
                system_name = f"Unknown Disc Image ({ext.upper()})"
                confidence = "low"
                confidence_score = 0.25
                warnings.append(f"{ext.upper()} 파일에서 기종 식별 시그니처를 찾지 못했습니다.")

        disc_info = DiscInfo(
            is_disc=True,
            disc_format=ext[1:].upper(),
            is_multi_file=True,
            is_complete=exists,
            referenced_files=[expected_companion],
            missing_files=missing,
            track_count=1
        )

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            file_ext=ext,
            system_id=system_id,
            system_name=system_name,
            system_type="console" if system_id != "unknown" else "unknown",
            platform_slug=cls._get_platform_slug(system_id),
            libretro_system=cls._get_libretro_system(system_id),
            is_arcade=False,
            is_disc=True,
            is_playable=exists,
            confidence=confidence,
            confidence_score=confidence_score,
            disc_info=disc_info,
            bios_info=cls._make_bios_info(system_id),
            header_metadata=HeaderMetadata(
                title=header_data.get("title") or stem,
                serial=header_data.get("serial"),
                region=header_data.get("region"),
                header_type=header_data.get("header_type")
            ),
            **_disc_evidence_kwargs(
                header_data, confidence_score, hint_reason=hint_reason,
                structural_method="disc_sheet", structural_detail=f"{ext.upper()} descriptor with companion {expected_companion}",
                structural_confidence=0.55 if exists else 0.25,
            ),
            summary=f"디스크 시트 {ext.upper()}: 보조 데이터 파일({companion_ext}) {'정상 확인' if exists else '누락됨'}",
            warnings=warnings
        )

    @classmethod
    def _analyze_raw_disc(cls, file_path: str, base_name: str, file_size: int, ext: str) -> RomAnalysisResult:
        """Standalone ISO / BIN / RVZ / WBFS 분석"""
        header_data = cls._scan_first_track(file_path)
        cls._enrich_from_serial(header_data)

        system_id = header_data.get("system_id")
        confidence = "high"
        confidence_score = 0.95
        warnings = []
        hint_reason = None

        if system_id:
            system_name = header_data.get("system_name") or f"Optical Disc Image ({system_id.upper()})"
        else:
            hint_res = cls._infer_system_from_hints(file_path)
            if hint_res:
                system_id, system_name, hint_reason = hint_res
                confidence = "medium"
                confidence_score = 0.65
                warnings.append(f"바이너리 시그니처 미검출; {hint_reason}를 기반으로 기종을 추정했습니다.")
            else:
                system_id = "unknown"
                system_name = f"Unknown Optical Disc Image ({ext.upper()})"
                confidence = "low"
                confidence_score = 0.25
                warnings.append(f"{ext.upper()} 파일에서 기종 식별 시그니처를 찾지 못했습니다. 수동 지정이 필요합니다.")

        disc_info = DiscInfo(
            is_disc=True,
            disc_format=ext[1:].upper(),
            is_multi_file=False,
            is_complete=True,
            referenced_files=[base_name],
            missing_files=[],
            track_count=1
        )

        return RomAnalysisResult(
            file_path=file_path,
            file_name=base_name,
            file_size=file_size,
            file_ext=ext,
            system_id=system_id,
            system_name=system_name,
            system_type="console" if system_id != "unknown" else "unknown",
            platform_slug=cls._get_platform_slug(system_id),
            libretro_system=cls._get_libretro_system(system_id),
            is_arcade=False,
            is_disc=True,
            is_playable=True,
            confidence=confidence,
            confidence_score=confidence_score,
            disc_info=disc_info,
            bios_info=cls._make_bios_info(system_id),
            header_metadata=HeaderMetadata(
                title=header_data.get("title") or os.path.splitext(base_name)[0],
                serial=header_data.get("serial"),
                region=header_data.get("region"),
                header_type=header_data.get("header_type")
            ),
            **_disc_evidence_kwargs(header_data, confidence_score, hint_reason=hint_reason),
            summary=f"단일 디스크 이미지 ({ext.upper()}): {system_name}",
            warnings=warnings
        )

    @classmethod
    def _scan_first_track(cls, track_path: str) -> Dict[str, Any]:
        """트랙 파일의 첫 수 MB를 읽어 바이너리 시그니처 분석"""
        if not os.path.exists(track_path):
            return {}
        try:
            with open(track_path, "rb") as f:
                # 앞부분 최대 4MB 탐색 (ISO9660 디렉터리 및 SYSTEM.CNF 위치 포함)
                data = f.read(4 * 1024 * 1024)
                return DiscSerialScanner.scan_binary_chunk(data)
        except Exception as exc:
            logger.debug("disc track scan failed for %s: %s", track_path, exc, exc_info=True)
            return {}

    @classmethod
    def _parse_param_sfo(cls, data: bytes) -> Dict[str, str]:
        """하위 호환용 별칭. 실제 파싱은 disc.parsers.pbp에서 수행한다."""
        return parse_param_sfo(data)

    @classmethod
    def _find_case_insensitive(cls, directory: str, filename: str) -> Optional[str]:
        """하위 호환용 별칭. 공통 parser 유틸리티를 사용한다."""
        return find_case_insensitive(directory, filename)

    @classmethod
    def _make_bios_info(cls, system_id: str) -> BiosInfo:
        """기종별 바이오스 요구사항 생성 (DB 우선 조회 -> 카탈로그 폴백)"""
        db_bio = query_bios_manifest(system_id)
        if db_bio:
            return BiosInfo(
                needs_bios=True,
                mandatory=db_bio.get("mandatory", True),
                bios_files=db_bio.get("bios_files", []),
                description=db_bio.get("description")
            )
        if system_id in CONSOLE_BIOS_CATALOG:
            cat = CONSOLE_BIOS_CATALOG[system_id]
            return BiosInfo(
                needs_bios=True,
                mandatory=cat.get("mandatory", True),
                bios_files=cat.get("bios_files", []),
                description=cat.get("description")
            )
        return BiosInfo(needs_bios=False)

    @classmethod
    def _get_libretro_system(cls, system_id: str) -> Optional[str]:
        mapping = {
            "psx": "Sony_-_PlayStation",
            "ps2": "Sony_-_PlayStation_2",
            "psp": "Sony_-_PlayStation_Portable",
            "saturn": "Sega_-_Saturn",
            "dreamcast": "Sega_-_Dreamcast",
            "segacd": "Sega_-_Mega-CD_-_Sega_CD",
            "pcecd": "NEC_-_PC_Engine_-_TurboGrafx_16",
            "3do": "The_3DO_Company_-_3DO",
            "gamecube": "Nintendo_-_GameCube",
            "wii": "Nintendo_-_Wii"
        }
        return mapping.get(system_id)

    @classmethod
    def _get_platform_slug(cls, system_id: str) -> str:
        return system_id

    @classmethod
    def _enrich_from_serial(cls, header_data: Dict[str, Any]):
        """시리얼 번호가 있는 경우 내장 DB에서 정식 타이틀 및 지역 보강"""
        serial = header_data.get("serial")
        if not serial:
            return
        info = query_disc_serial(serial)
        header_data["_serial_db_match"] = bool(info)
        if info:
            if not header_data.get("title") or len(header_data.get("title", "")) < 3:
                header_data["title"] = info.get("title")
            if info.get("system_id"):
                header_data["system_id"] = info.get("system_id")
            if info.get("region") and not header_data.get("region"):
                header_data["region"] = info.get("region")

    @classmethod
    def _infer_system_from_hints(cls, file_path: str) -> Optional[Tuple[str, str, str]]:
        """
        바이너리 시그니처 미검출 시, 파일명 태그 및 상위 폴더 경로 힌트를 분석하여 기종 추론.
        반환: (system_id, system_name, hint_reason)
        """
        abs_path = os.path.abspath(file_path)
        base_name = os.path.basename(abs_path)
        parent_dirs = [p.lower() for p in os.path.dirname(abs_path).replace("\\", "/").split("/") if p]

        # 1. 파일명 태그 정규식 힌트 (예: [PSX], (PS1), [Saturn], [Dreamcast], [GameCube])
        tag_patterns = [
            (r"[\[\(](?:psx|ps1|playstation)[\]\)]", "psx", "Sony PlayStation", "파일명 태그 '[PS1/PSX]'"),
            (r"[\[\(](?:ps2|playstation\s*2)[\]\)]", "ps2", "Sony PlayStation 2", "파일명 태그 '[PS2]'"),
            (r"[\[\(](?:psp|playstation\s*portable)[\]\)]", "psp", "Sony PlayStation Portable", "파일명 태그 '[PSP]'"),
            (r"[\[\(](?:saturn|ss)[\]\)]", "saturn", "Sega Saturn", "파일명 태그 '[Saturn]'"),
            (r"[\[\(](?:dreamcast|dc)[\]\)]", "dreamcast", "Sega Dreamcast", "파일명 태그 '[Dreamcast]'"),
            (r"[\[\(](?:segacd|megacd|sega\s*cd|mega\s*cd)[\]\)]", "segacd", "Sega CD / Mega CD", "파일명 태그 '[Sega CD]'"),
            (r"[\[\(](?:pcecd|turbocd|tg16cd|pc-engine\s*cd)[\]\)]", "pcecd", "PC Engine CD / TurboGrafx-CD", "파일명 태그 '[PCE CD]'"),
            (r"[\[\(](?:3do)[\]\)]", "3do", "Panasonic 3DO", "파일명 태그 '[3DO]'"),
            (r"[\[\(](?:gamecube|gc)[\]\)]", "gamecube", "Nintendo GameCube", "파일명 태그 '[GameCube]'"),
            (r"[\[\(](?:wii)[\]\)]", "wii", "Nintendo Wii", "파일명 태그 '[Wii]'"),
        ]

        for pat, sid, sname, reason in tag_patterns:
            if re.search(pat, base_name, re.IGNORECASE):
                return sid, f"{sname} (태그 힌트 추정)", reason

        # 2. 디렉토리/폴더명 힌트 (예: roms/psx/..., library/saturn/..., /dreamcast/...)
        dir_hints = [
            (["psx", "ps1", "playstation"], "psx", "Sony PlayStation", "상위 폴더 경로 'psx'"),
            (["ps2", "playstation2"], "ps2", "Sony PlayStation 2", "상위 폴더 경로 'ps2'"),
            (["psp"], "psp", "Sony PlayStation Portable", "상위 폴더 경로 'psp'"),
            (["saturn", "segasaturn"], "saturn", "Sega Saturn", "상위 폴더 경로 'saturn'"),
            (["dreamcast", "dc"], "dreamcast", "Sega Dreamcast", "상위 폴더 경로 'dreamcast'"),
            (["segacd", "megacd"], "segacd", "Sega CD / Mega CD", "상위 폴더 경로 'segacd'"),
            (["pcecd", "turbocd", "turbografxcd"], "pcecd", "PC Engine CD / TurboGrafx-CD", "상위 폴더 경로 'pcecd'"),
            (["3do"], "3do", "Panasonic 3DO", "상위 폴더 경로 '3do'"),
            (["gamecube", "gc"], "gamecube", "Nintendo GameCube", "상위 폴더 경로 'gamecube'"),
            (["wii"], "wii", "Nintendo Wii", "상위 폴더 경로 'wii'"),
        ]

        for keywords, sid, sname, reason in dir_hints:
            for folder in parent_dirs[-3:]:
                if folder in keywords:
                    return sid, f"{sname} (경로 힌트 추정)", reason

        return None
