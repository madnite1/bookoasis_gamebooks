# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# MAME CHD 포맷의 버전별 고정 헤더 크기.
_CHD_HEADER_SIZES = {1: 76, 2: 80, 3: 120, 4: 108, 5: 124}
_MAX_METADATA_ENTRIES = 256
_MAX_METADATA_BYTES = 1024 * 1024

# 광학 디스크 계열에서 흔히 사용되는 CHD metadata tags.
_OPTICAL_METADATA_TAGS = {
    "CHTR",  # CD-ROM track metadata (legacy)
    "CHT2",  # CD-ROM track metadata v2
    "CHGD",  # GD-ROM track metadata
}


@dataclass
class ChdMetadataEntry:
    tag: str
    flags: int
    length: int
    offset: int
    next_offset: int
    text: Optional[str] = None


@dataclass
class ChdParseResult:
    magic_valid: bool = False
    header_valid: bool = False
    header_length: int = 0
    version: int = 0
    logical_bytes: int = 0
    map_offset: int = 0
    metadata_offset: int = 0
    hunk_bytes: int = 0
    unit_bytes: int = 0
    compressors: List[str] = field(default_factory=list)
    metadata_entries: List[ChdMetadataEntry] = field(default_factory=list)
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    optical_media: Optional[str] = None  # cdrom | gdrom | None
    header_data: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _fourcc(raw: bytes) -> str:
    if len(raw) != 4:
        return ""
    if raw == b"\x00" * 4:
        return ""
    return raw.decode("ascii", errors="replace")


def _parse_v5_header(fh, file_size: int, result: ChdParseResult) -> None:
    fh.seek(16)
    rest = fh.read(108)
    if len(rest) != 108:
        result.error = "CHD v5 헤더가 124바이트보다 짧습니다."
        return

    result.compressors = [
        value for value in (_fourcc(rest[i:i + 4]) for i in range(0, 16, 4)) if value
    ]
    result.logical_bytes = int.from_bytes(rest[16:24], "big")
    result.map_offset = int.from_bytes(rest[24:32], "big")
    result.metadata_offset = int.from_bytes(rest[32:40], "big")
    result.hunk_bytes = int.from_bytes(rest[40:44], "big")
    result.unit_bytes = int.from_bytes(rest[44:48], "big")

    problems = []
    if result.logical_bytes <= 0:
        problems.append("logical_bytes=0")
    if result.hunk_bytes <= 0:
        problems.append("hunk_bytes=0")
    if result.unit_bytes <= 0:
        problems.append("unit_bytes=0")
    if result.hunk_bytes and result.unit_bytes and result.hunk_bytes < result.unit_bytes:
        problems.append("hunk_bytes < unit_bytes")
    if not (result.header_length <= result.map_offset < file_size):
        problems.append(f"map_offset 범위 오류({result.map_offset})")
    if result.metadata_offset and not (result.header_length <= result.metadata_offset < file_size):
        problems.append(f"metadata_offset 범위 오류({result.metadata_offset})")

    if problems:
        result.error = "CHD v5 헤더 필드가 올바르지 않습니다: " + ", ".join(problems)
        return

    result.header_valid = True



def _parse_track_metadata(tag: str, text: Optional[str]) -> Optional[Dict[str, Any]]:
    if tag not in _OPTICAL_METADATA_TAGS or not text:
        return None
    fields: Dict[str, Any] = {"metadata_tag": tag}
    for token in text.split():
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            continue
        if key in {"track", "frames", "pregap", "postgap"}:
            try:
                fields[key] = int(value)
                continue
            except ValueError:
                pass
        fields[key] = value
    return fields if len(fields) > 1 else None

def _parse_metadata_chain(fh, file_size: int, result: ChdParseResult) -> None:
    offset = result.metadata_offset
    seen = set()
    total_payload = 0

    while offset and len(result.metadata_entries) < _MAX_METADATA_ENTRIES:
        if offset in seen:
            result.warnings.append("CHD metadata 체인에 순환 참조가 있습니다.")
            break
        seen.add(offset)
        if offset < result.header_length or offset + 16 > file_size:
            result.warnings.append(f"CHD metadata offset이 파일 범위를 벗어납니다: {offset}")
            break

        fh.seek(offset)
        header = fh.read(16)
        if len(header) != 16:
            result.warnings.append(f"CHD metadata 헤더를 끝까지 읽지 못했습니다: {offset}")
            break

        tag = _fourcc(header[:4])
        flags_length = int.from_bytes(header[4:8], "big")
        flags = (flags_length >> 24) & 0xFF
        length = flags_length & 0x00FFFFFF
        next_offset = int.from_bytes(header[8:16], "big")
        payload_offset = offset + 16

        if length > _MAX_METADATA_BYTES or payload_offset + length > file_size:
            result.warnings.append(f"CHD metadata payload 범위가 올바르지 않습니다: {tag}@{offset} len={length}")
            break
        total_payload += length
        if total_payload > _MAX_METADATA_BYTES:
            result.warnings.append("CHD metadata 전체 크기가 안전 한도를 초과했습니다.")
            break

        payload = fh.read(length)
        text = None
        if payload and all((b in (9, 10, 13) or 32 <= b <= 126) for b in payload.rstrip(b"\x00")):
            text = payload.rstrip(b"\x00").decode("ascii", errors="replace")

        result.metadata_entries.append(ChdMetadataEntry(
            tag=tag,
            flags=flags,
            length=length,
            offset=offset,
            next_offset=next_offset,
            text=text,
        ))
        track = _parse_track_metadata(tag, text)
        if track:
            result.tracks.append(track)
        offset = next_offset

    if len(result.metadata_entries) >= _MAX_METADATA_ENTRIES and offset:
        result.warnings.append("CHD metadata 엔트리 수가 안전 한도를 초과했습니다.")

    tags = {entry.tag for entry in result.metadata_entries}
    if "CHGD" in tags:
        result.optical_media = "gdrom"
    elif tags & (_OPTICAL_METADATA_TAGS - {"CHGD"}):
        result.optical_media = "cdrom"


def parse_chd(file_path: str, scan_limit: int = 1024 * 1024) -> ChdParseResult:
    result = ChdParseResult(header_data={"system_id": None, "system_name": None})
    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as fh:
            fixed = fh.read(16)
            if len(fixed) < 16:
                result.error = "CHD 헤더가 16바이트보다 짧습니다."
                return result

            result.magic_valid = fixed[:8] == b"MComprHD"
            if not result.magic_valid:
                result.error = "CHD magic(MComprHD)이 올바르지 않습니다."
                return result

            result.header_length = int.from_bytes(fixed[8:12], "big")
            result.version = int.from_bytes(fixed[12:16], "big")
            expected_length = _CHD_HEADER_SIZES.get(result.version)
            if not expected_length or result.header_length != expected_length or file_size < result.header_length:
                result.error = (
                    f"CHD 헤더 구조가 올바르지 않습니다: version={result.version}, "
                    f"header_length={result.header_length}, file_size={file_size}"
                )
                return result

            if result.version == 5:
                _parse_v5_header(fh, file_size, result)
                if not result.header_valid:
                    return result
                if result.metadata_offset:
                    _parse_metadata_chain(fh, file_size, result)
            else:
                # v1~v4는 현재 고정 헤더 크기와 파일 범위까지만 보수적으로 검증한다.
                result.header_valid = True

            # CHD payload는 압축된 컨테이너 데이터다. decoder 없이 raw 바이트를 디스크
            # sector처럼 시리얼 스캔하면 우연한 문자열을 강한 근거로 오인할 수 있으므로
            # 실제 콘텐츠 분석은 수행하지 않는다. scan_limit 인자는 API 호환용으로 유지한다.
            _ = scan_limit
    except OSError as exc:
        logger.debug("CHD metadata scan failed for %s: %s", file_path, exc, exc_info=True)
        result.error = f"CHD 메타데이터 분석 실패: {type(exc).__name__}: {exc}"
    return result
