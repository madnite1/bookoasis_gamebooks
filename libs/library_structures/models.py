# -*- coding: utf-8 -*-
"""Library Structures 데이터 모델 정의."""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class LibraryPaths:
    """Game Books 파일 시스템 디렉터리 경로 명세."""
    root: str
    library_dir: str
    resources_dir: str
    assets_dir: str
    config_dir: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class SaveResult:
    """라이브러리 파일 배치/저장 결과 객체."""
    success: bool = True
    item_type: str = "rom"
    platform_slug: str = "unknown"
    game_id: Optional[str] = None
    rom_dest_path: Optional[str] = None
    bios_dest_path: Optional[str] = None
    companion_dest_paths: List[str] = field(default_factory=list)
    cover_s_dest_path: Optional[str] = None
    cover_l_dest_path: Optional[str] = None
    screenshots_dest_paths: List[str] = field(default_factory=list)
    metadata_dest_path: Optional[str] = None
    save_dest_path: Optional[str] = None
    state_dest_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def cover_dest_path(self) -> Optional[str]:
        return self.cover_l_dest_path or self.cover_s_dest_path

    def add_error(self, message: str):
        if message:
            self.success = False
            self.errors.append(str(message))

    def absorb(self, other: "SaveResult"):
        """복합 저장에서 하위 작업 결과를 현재 결과에 합친다."""
        if not other:
            return self
        self.success = self.success and other.success
        for attr in (
            "rom_dest_path",
            "bios_dest_path",
            "cover_s_dest_path",
            "cover_l_dest_path",
            "metadata_dest_path",
            "save_dest_path",
            "state_dest_path",
        ):
            value = getattr(other, attr, None)
            if value:
                setattr(self, attr, value)
        self.companion_dest_paths.extend(other.companion_dest_paths)
        self.screenshots_dest_paths.extend(other.screenshots_dest_paths)
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["cover_dest_path"] = self.cover_dest_path
        return data
