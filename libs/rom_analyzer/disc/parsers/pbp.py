# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
import logging
import os
import struct
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PbpParseResult:
    header_valid: bool = False
    magic_valid: bool = False
    system_id: str = "unknown"
    system_name: str = "Invalid or Truncated PBP Package"
    title: str = ""
    serial: Optional[str] = None
    header_data: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


def parse_param_sfo(data: bytes) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    if len(data) < 20 or data[:4] != b"\x00PSF":
        return parsed
    try:
        key_table_offset, data_table_offset, entry_count = struct.unpack("<III", data[8:20])
        pos = 20
        if key_table_offset > len(data) or data_table_offset > len(data):
            raise ValueError("PARAM.SFO table offset exceeds data size")
        for _ in range(entry_count):
            if pos + 16 > len(data):
                raise ValueError("truncated PARAM.SFO entry table")
            key_offset, _param_fmt, param_len, _param_max_len, val_offset = struct.unpack(
                "<HHIII", data[pos:pos+16]
            )
            pos += 16
            key_start = key_table_offset + key_offset
            if key_start >= len(data):
                raise ValueError("PARAM.SFO key offset exceeds data size")
            key_end = data.find(b"\x00", key_start)
            if key_end < 0:
                raise ValueError("unterminated PARAM.SFO key")
            value_start = data_table_offset + val_offset
            value_end = value_start + param_len
            if value_end > len(data):
                raise ValueError("PARAM.SFO value exceeds data size")
            key = data[key_start:key_end].decode("ascii", errors="ignore")
            value = data[value_start:value_end].rstrip(b"\x00").decode("utf-8", errors="ignore")
            parsed[key] = value
    except (ValueError, struct.error) as exc:
        logger.debug("PARAM.SFO parse failed: %s", exc, exc_info=True)
    return parsed


def parse_pbp(file_path: str, file_size: Optional[int] = None, default_title: Optional[str] = None) -> PbpParseResult:
    result = PbpParseResult(title=default_title or os.path.splitext(os.path.basename(file_path))[0])
    file_size = file_size if file_size is not None else os.path.getsize(file_path)
    try:
        with open(file_path, "rb") as fh:
            magic = fh.read(4)
            if magic != b"\x00PBP":
                raise ValueError("invalid PBP magic")
            result.magic_valid = True

            version = fh.read(4)
            offset_bytes = fh.read(32)
            if len(version) != 4 or len(offset_bytes) != 32:
                raise ValueError("truncated PBP header")
            offsets = struct.unpack("<8I", offset_bytes)
            if offsets[0] < 40:
                raise ValueError(f"invalid PARAM.SFO offset: {offsets[0]}")
            if any(a > b for a, b in zip(offsets, offsets[1:])):
                raise ValueError("PBP section offsets are not monotonic")
            if offsets[-1] > file_size:
                raise ValueError(f"PBP section offset exceeds file size: {offsets[-1]} > {file_size}")
            result.header_valid = True
            result.system_id = "psx"
            result.system_name = "Sony PlayStation (PS1 EBOOT)"

            if offsets[1] > offsets[0]:
                fh.seek(offsets[0])
                parsed = parse_param_sfo(fh.read(offsets[1] - offsets[0]))
                result.header_data.update(parsed)
                result.title = parsed.get("TITLE") or result.title
                result.serial = parsed.get("DISC_ID") or None
                if parsed.get("CATEGORY") == "UG" or (result.serial and result.serial.startswith("U")):
                    result.system_id = "psp"
                    result.system_name = "Sony PlayStation Portable (PSP)"
    except (OSError, ValueError, struct.error) as exc:
        logger.debug("PBP metadata parse failed for %s: %s", file_path, exc, exc_info=True)
        result.header_valid = False
        result.system_id = "unknown"
        result.system_name = "Invalid or Truncated PBP Package"
        result.error = f"PBP 메타데이터 파싱 실패: {type(exc).__name__}: {exc}"
    return result
