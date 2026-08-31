# -*- coding: utf-8 -*-
"""rom_database의 공개 facade."""

from typing import Optional

from .paths import DatabasePaths, PathLike
from .repositories import CompatibilityRepository, DatRepository, MetadataRepository


class RomDatabase:
    """ROM 참조 DB 세 종류를 일관된 API로 묶는다."""

    def __init__(
        self,
        data_dir: Optional[PathLike] = None,
        metadata_path: Optional[PathLike] = None,
        dat_path: Optional[PathLike] = None,
        compatibility_path: Optional[PathLike] = None,
    ):
        self.paths = DatabasePaths.build(
            data_dir=data_dir,
            metadata_path=metadata_path,
            dat_path=dat_path,
            compatibility_path=compatibility_path,
        )
        self.metadata = MetadataRepository(self.paths.metadata)
        self.dat = DatRepository(self.paths.dat)
        self.compatibility = CompatibilityRepository(self.paths.compatibility)

    @classmethod
    def default(cls) -> "RomDatabase":
        return get_default_database()

    def availability(self) -> dict:
        return {
            "metadata": self.metadata.is_available(),
            "dat": self.dat.is_available(),
            "compatibility": self.compatibility.is_available(),
        }


_DEFAULT_DATABASE: Optional[RomDatabase] = None


def get_default_database() -> RomDatabase:
    global _DEFAULT_DATABASE
    if _DEFAULT_DATABASE is None:
        _DEFAULT_DATABASE = RomDatabase()
    return _DEFAULT_DATABASE
