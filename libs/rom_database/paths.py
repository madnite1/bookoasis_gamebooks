# -*- coding: utf-8 -*-
"""rom_database에서 사용하는 데이터 파일 경로 정의."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


@dataclass(frozen=True)
class DatabasePaths:
    """세 종류의 참조 DB 경로를 한 묶음으로 관리한다."""

    metadata: Path
    dat: Path
    compatibility: Path

    @classmethod
    def default(cls) -> "DatabasePaths":
        return cls.from_data_dir(Path(__file__).resolve().parent / "data")

    @classmethod
    def from_data_dir(cls, data_dir: PathLike) -> "DatabasePaths":
        base = Path(data_dir).expanduser().resolve()
        return cls(
            metadata=base / "rom_metadata.db",
            dat=base / "arcade_dat.db",
            compatibility=base / "mame_compatibility.db",
        )

    @classmethod
    def build(
        cls,
        data_dir: Optional[PathLike] = None,
        metadata_path: Optional[PathLike] = None,
        dat_path: Optional[PathLike] = None,
        compatibility_path: Optional[PathLike] = None,
    ) -> "DatabasePaths":
        base = cls.from_data_dir(data_dir) if data_dir is not None else cls.default()
        return cls(
            metadata=Path(metadata_path).expanduser().resolve() if metadata_path else base.metadata,
            dat=Path(dat_path).expanduser().resolve() if dat_path else base.dat,
            compatibility=(
                Path(compatibility_path).expanduser().resolve()
                if compatibility_path
                else base.compatibility
            ),
        )
