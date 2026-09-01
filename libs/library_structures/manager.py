# -*- coding: utf-8 -*-
"""Game Books 라이브러리 물리 배치 메인 진입점."""

from typing import Optional, Dict, Any, Union, List

from rom_analyzer.models import RomAnalysisResult
from .base import BaseLibraryStructure, GameKey, ImageSource
from .romm import RomMLibraryStructure
from .models import SaveResult, LibraryPaths


class LibraryManager:
    """Game Books가 사용하는 통합 라이브러리 구조 매니저."""

    STRUCTURE_MAP = {
        "romm": RomMLibraryStructure,
    }

    def __init__(self, root_dir: str, structure_type: str = "romm", **kwargs):
        self.root_dir = root_dir
        self.structure_type = structure_type.lower()
        cls_structure = self.STRUCTURE_MAP.get(self.structure_type, RomMLibraryStructure)
        self.structure: BaseLibraryStructure = cls_structure(root_dir, **kwargs)

    def init_structure(self) -> LibraryPaths:
        return self.structure.init_structure()

    def get_paths(self) -> LibraryPaths:
        return self.structure.get_paths()

    def place_content(
        self,
        rom_info: RomAnalysisResult,
        game_id: GameKey,
        move_files: bool = True,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        return self.structure.place_content(
            rom_info=rom_info,
            game_id=game_id,
            move_files=move_files,
            conflict_strategy=conflict_strategy,
        )

    def place_bios(
        self,
        rom_info: RomAnalysisResult,
        move_files: bool = True,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        return self.structure.place_bios(
            rom_info=rom_info,
            move_files=move_files,
            conflict_strategy=conflict_strategy,
        )

    def save_cover(self, game_id: GameKey, cover_data: ImageSource) -> SaveResult:
        return self.structure.save_cover(game_id, cover_data)

    def save_user_save(
        self,
        user_id: GameKey,
        game_id: GameKey,
        data: bytes,
        extension: str = ".sav",
    ) -> SaveResult:
        return self.structure.save_user_save(user_id, game_id, data, extension=extension)

    def save_user_state(
        self,
        user_id: GameKey,
        game_id: GameKey,
        data: bytes,
        slot: int = 0,
    ) -> SaveResult:
        return self.structure.save_user_state(user_id, game_id, data, slot=slot)

    def save(
        self,
        rom_info: RomAnalysisResult,
        move_files: bool = True,
        cover_data: Optional[ImageSource] = None,
        screenshots: Optional[List[ImageSource]] = None,
        metadata: Optional[Union[Dict[str, Any], str]] = None,
        save_metadata: bool = True,
        save_data: Optional[bytes] = None,
        state_data: Optional[bytes] = None,
        user_id: str = "default",
        rom_identifier: Optional[str] = None,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        """기존 호환 API. 새 코드는 독립 메서드를 우선 사용한다."""
        return self.structure.save(
            rom_info=rom_info,
            move_files=move_files,
            cover_data=cover_data,
            screenshots=screenshots,
            metadata=metadata,
            save_metadata=save_metadata,
            save_data=save_data,
            state_data=state_data,
            user_id=user_id,
            rom_identifier=rom_identifier,
            conflict_strategy=conflict_strategy,
        )


def create_library(root_dir: str, structure_type: str = "romm", **kwargs) -> LibraryManager:
    return LibraryManager(root_dir, structure_type=structure_type, **kwargs)
