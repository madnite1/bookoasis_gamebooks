# -*- coding: utf-8 -*-
import os
from typing import Optional


def find_case_insensitive(directory: str, filename: str) -> Optional[str]:
    """같은 디렉터리에서 대소문자만 다른 파일명을 찾아 반환한다."""
    if not os.path.isdir(directory):
        return None
    target = filename.lower()
    try:
        for entry in os.listdir(directory):
            if entry.lower() == target:
                return os.path.join(directory, entry)
    except OSError:
        return None
    return None


def resolve_case_insensitive_relative(base_dir: str, relative_path: str) -> Optional[str]:
    """base_dir 내부 상대경로를 컴포넌트별 대소문자 무시 방식으로 해석한다."""
    current = os.path.abspath(base_dir)
    normalized = os.path.normpath((relative_path or "").replace("\\", os.sep))
    if normalized in {"", ".", ".."} or os.path.isabs(normalized) or normalized.startswith(".." + os.sep):
        return None
    for part in normalized.split(os.sep):
        if part in {"", ".", ".."} or not os.path.isdir(current):
            return None
        match = None
        try:
            for entry in os.listdir(current):
                if entry.casefold() == part.casefold():
                    match = entry
                    break
        except OSError:
            return None
        if match is None:
            return None
        current = os.path.join(current, match)
    return current
