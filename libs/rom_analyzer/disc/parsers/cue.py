# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
import logging
import os
import re
from typing import List, Optional

from .common import find_case_insensitive

logger = logging.getLogger(__name__)
CUE_FILE_RE = re.compile(r'^\s*FILE\s+["\']?([^"\']+)["\']?\s+([A-Za-z0-9]+)', re.IGNORECASE)


@dataclass
class CueParseResult:
    referenced_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    first_data_file: Optional[str] = None
    error: Optional[str] = None


def parse_cue(file_path: str, base_dir: Optional[str] = None) -> CueParseResult:
    result = CueParseResult()
    base_dir = base_dir or os.path.dirname(os.path.abspath(file_path))
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                match = CUE_FILE_RE.match(line)
                if not match:
                    continue
                rel_file = match.group(1).strip()
                file_type = match.group(2).strip().upper()
                result.referenced_files.append(rel_file)

                target_path = os.path.join(base_dir, rel_file)
                if not os.path.exists(target_path):
                    target_path = find_case_insensitive(base_dir, rel_file) or target_path
                    if not os.path.exists(target_path):
                        result.missing_files.append(rel_file)

                if (
                    result.first_data_file is None
                    and file_type == "BINARY"
                    and os.path.exists(target_path)
                ):
                    result.first_data_file = target_path
    except (OSError, UnicodeError) as exc:
        logger.debug("CUE descriptor parse failed for %s: %s", file_path, exc, exc_info=True)
        result.error = f"CUE 시트 파싱 실패: {type(exc).__name__}: {exc}"
    return result
