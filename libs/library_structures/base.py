# -*- coding: utf-8 -*-
"""Library Structure 추상 기본 클래스."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Union, List

from rom_analyzer.models import RomAnalysisResult
from .models import SaveResult, LibraryPaths


GameKey = Union[int, str]
ImageSource = Union[bytes, str]


class BaseLibraryStructure(ABC):
    """Game Books 라이브러리의 물리 파일 배치 계약."""

    def __init__(self, root_dir: str):
        self.root_dir = root_dir

    @abstractmethod
    def init_structure(self) -> LibraryPaths:
        """라이브러리 필수 디렉터리 생성 및 경로 반환."""
        raise NotImplementedError

    @abstractmethod
    def get_paths(self) -> LibraryPaths:
        """현재 라이브러리 경로 정보 반환."""
        raise NotImplementedError

    @abstractmethod
    def place_content(
        self,
        rom_info: RomAnalysisResult,
        game_id: GameKey,
        move_files: bool = True,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        """ROM 또는 멀티파일 번들을 game_id 기반 콘텐츠 디렉터리에 배치."""
        raise NotImplementedError

    @abstractmethod
    def place_bios(
        self,
        rom_info: RomAnalysisResult,
        move_files: bool = True,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        """BIOS/펌웨어 파일을 플랫폼 BIOS 디렉터리에 배치."""
        raise NotImplementedError

    @abstractmethod
    def save_cover(self, game_id: GameKey, cover_data: ImageSource) -> SaveResult:
        """게임 ID 리소스 디렉터리에 large/small WebP 커버를 저장."""
        raise NotImplementedError

    @abstractmethod
    def save_user_save(
        self,
        user_id: GameKey,
        game_id: GameKey,
        data: bytes,
        extension: str = ".sav",
    ) -> SaveResult:
        """사용자 배터리 세이브를 게임 ID 기반 경로에 저장."""
        raise NotImplementedError

    @abstractmethod
    def save_user_state(
        self,
        user_id: GameKey,
        game_id: GameKey,
        data: bytes,
        slot: int = 0,
    ) -> SaveResult:
        """사용자 세이브스테이트를 게임 ID 기반 경로에 저장."""
        raise NotImplementedError

    @abstractmethod
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
        """기존 호환용 복합 저장 API. 내부적으로 독립 물리 배치 API를 조합한다."""
        raise NotImplementedError
