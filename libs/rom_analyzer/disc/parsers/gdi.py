# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
import logging
import os
import re
from typing import List, Optional

from .common import find_case_insensitive

logger = logging.getLogger(__name__)
GDI_TRACK_RE = re.compile(r'^\s*\d+\s+\d+\s+\d+\s+\d+\s+["\']?([^"\']+)["\']?\s+\d+', re.IGNORECASE)


@dataclass
class GdiParseResult:
    referenced_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    first_track_path: Optional[str] = None
    declared_track_count: Optional[int] = None
    error: Optional[str] = None


def parse_gdi(file_path: str, base_dir: Optional[str] = None) -> GdiParseResult:
    result = GdiParseResult()
    base_dir = base_dir or os.path.dirname(os.path.abspath(file_path))
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = [line.strip() for line in fh if line.strip()]
        if lines:
            try:
                result.declared_track_count = int(lines[0])
            except ValueError:
                result.declared_track_count = None
            for line in lines[1:]:
                match = GDI_TRACK_RE.match(line)
                if not match:
                    continue
                track_file = match.group(1).strip()
                result.referenced_files.append(track_file)
                target_path = os.path.join(base_dir, track_file)
                if not os.path.exists(target_path):
                    target_path = find_case_insensitive(base_dir, track_file) or target_path
                    if not os.path.exists(target_path):
                        result.missing_files.append(track_file)
                if result.first_track_path is None and os.path.exists(target_path):
                    result.first_track_path = target_path
    except (OSError, UnicodeError) as exc:
        logger.debug("GDI descriptor parse failed for %s: %s", file_path, exc, exc_info=True)
        result.error = f"GDI 시트 파싱 실패: {type(exc).__name__}: {exc}"
    return result
