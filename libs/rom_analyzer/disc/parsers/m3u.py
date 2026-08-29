# -*- coding: utf-8 -*-
"""M3U 멀티디스크 플레이리스트 파서."""

from dataclasses import dataclass, field
import logging
import os
import re
from typing import List, Optional

from .common import resolve_case_insensitive_relative

logger = logging.getLogger(__name__)

# Libretro 계열 멀티디스크 플레이리스트에서 실제로 사용할 수 있는 디스크 엔트리.
M3U_DISC_EXTENSIONS = {
    ".chd", ".cue", ".ccd", ".mds", ".pbp", ".iso", ".bin", ".img", ".cso",
}
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass
class M3uParseResult:
    referenced_files: List[str] = field(default_factory=list)
    resolved_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    invalid_references: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _decode_playlist(data: bytes) -> str:
    last_error = None
    for encoding in ("utf-8-sig", "cp949", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return ""


def parse_m3u(file_path: str, base_dir: Optional[str] = None) -> M3uParseResult:
    """M3U의 상대경로 엔트리를 안전하게 해석하고 실제 파일 존재 여부를 검사한다."""
    result = M3uParseResult()
    base_dir = os.path.abspath(base_dir or os.path.dirname(os.path.abspath(file_path)))
    seen = set()

    try:
        with open(file_path, "rb") as fh:
            text = _decode_playlist(fh.read())

        for raw_line in text.splitlines():
            raw_ref = raw_line.strip()
            if not raw_ref or raw_ref.startswith("#"):
                continue
            if len(raw_ref) >= 2 and raw_ref[0] == raw_ref[-1] and raw_ref[0] in {'"', "'"}:
                raw_ref = raw_ref[1:-1].strip()
            if not raw_ref:
                continue

            # URL/절대경로/드라이브 경로와 상위 디렉터리 탈출을 허용하지 않는다.
            if "://" in raw_ref or os.path.isabs(raw_ref) or _DRIVE_RE.match(raw_ref):
                result.invalid_references.append(raw_ref)
                continue

            normalized = os.path.normpath(raw_ref.replace("\\", os.sep))
            if normalized in {"", ".", ".."} or normalized.startswith(".." + os.sep):
                result.invalid_references.append(raw_ref)
                continue

            ext = os.path.splitext(normalized)[1].lower()
            if ext not in M3U_DISC_EXTENSIONS:
                result.invalid_references.append(raw_ref)
                continue

            absolute = os.path.abspath(os.path.join(base_dir, normalized))
            try:
                inside = os.path.commonpath([base_dir, absolute]) == base_dir
            except ValueError:
                inside = False
            if not inside:
                result.invalid_references.append(raw_ref)
                continue

            display_ref = normalized.replace(os.sep, "/")
            key = display_ref.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.referenced_files.append(display_ref)

            resolved = absolute if os.path.exists(absolute) else resolve_case_insensitive_relative(base_dir, normalized)
            if resolved and os.path.isfile(resolved):
                result.resolved_files.append(resolved)
            else:
                result.resolved_files.append(absolute)
                result.missing_files.append(display_ref)
    except (OSError, UnicodeError) as exc:
        logger.debug("M3U playlist parse failed for %s: %s", file_path, exc, exc_info=True)
        result.error = f"M3U 플레이리스트 파싱 실패: {type(exc).__name__}: {exc}"

    return result
