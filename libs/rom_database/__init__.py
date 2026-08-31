# -*- coding: utf-8 -*-
"""ROM 참조 데이터베이스 접근 계층.

ROM 식별에 필요한 메타데이터, DAT, 코어 호환성 같은 원시 참조 데이터만 제공한다.
실행 가능성이나 identity 등급 같은 해석은 rom_analyzer가 담당한다.
"""

from .manager import RomDatabase, get_default_database
from .models import CoreCompatibilityRecord
from .paths import DatabasePaths

__all__ = [
    "RomDatabase",
    "get_default_database",
    "CoreCompatibilityRecord",
    "DatabasePaths",
]
