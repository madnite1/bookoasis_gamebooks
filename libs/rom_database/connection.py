# -*- coding: utf-8 -*-
"""SQLite 참조 DB를 읽기 전용으로 여는 공통 유틸리티."""

import sqlite3
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


def open_readonly(path: PathLike) -> Optional[sqlite3.Connection]:
    """존재하는 SQLite 파일을 immutable/read-only 연결로 연다."""
    db_path = Path(path)
    if not db_path.is_file():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection
