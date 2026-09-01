# -*- coding: utf-8 -*-
"""
Game Books Library Structures.
ROM, BIOS, 커버, 세이브 및 메타데이터의 물리 라이브러리 배치를 담당한다.
디렉터리 구조 설계 시 RomM의 파일 시스템 관리 방식을 참고했다.
"""

from .models import SaveResult, LibraryPaths
from .base import BaseLibraryStructure
from .romm import RomMLibraryStructure
from .manager import LibraryManager, create_library

__all__ = [
    "LibraryManager",
    "create_library",
    "RomMLibraryStructure",
    "BaseLibraryStructure",
    "SaveResult",
    "LibraryPaths",
]
