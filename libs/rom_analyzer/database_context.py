# -*- coding: utf-8 -*-
"""분석 실행 단위의 RomDatabase 컨텍스트 관리.

ContextVar를 사용하여 커스텀 참조 DB를 현재 분석 흐름에만 적용한다.
rom_database는 이 모듈을 참조하지 않으므로 의존 방향은 유지된다.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from rom_database import RomDatabase, get_default_database


_ACTIVE_DATABASE: ContextVar[Optional[RomDatabase]] = ContextVar(
    "rom_analyzer_active_database",
    default=None,
)


def get_active_database() -> RomDatabase:
    """현재 분석 컨텍스트의 DB 또는 프로세스 기본 DB를 반환한다."""
    return _ACTIVE_DATABASE.get() or get_default_database()


@contextmanager
def use_database(database: Optional[RomDatabase]) -> Iterator[RomDatabase]:
    """현재 실행 컨텍스트에만 참조 DB를 임시로 적용한다."""
    active = database or get_default_database()
    token = _ACTIVE_DATABASE.set(active)
    try:
        yield active
    finally:
        _ACTIVE_DATABASE.reset(token)
