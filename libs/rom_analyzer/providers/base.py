# -*- coding: utf-8 -*-
"""
외부 메타데이터 프로바이더(ScreenScraper, IGDB 등) 공통 모델 및 인터페이스.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ScrapedArtwork:
    """스크랩된 이미지/아트웤 정보"""
    box_2d: Optional[str] = None          # 2D 전면 박스아트 URL
    box_3d: Optional[str] = None          # 3D 입체 박스아트 URL
    screenshot: Optional[str] = None      # 인게임 스크린샷 URL
    title_screen: Optional[str] = None    # 타이틀 화면 스크린샷 URL
    wheel: Optional[str] = None           # 투명 로고(Wheel) URL
    marquee: Optional[str] = None         # 아케이드 마키(Marquee) URL
    fanart: Optional[str] = None          # 팬아트/배경 이미지 URL


@dataclass
class ScrapedGameMetadata:
    """외부 메타데이터 제공자로부터 수집된 통합 게임 정보"""
    provider: str                         # screenscraper | igdb
    game_id: Optional[str] = None         # 외부 시스템 게임 고유 ID
    title: str = ""                       # 정식 게임명
    title_korean: Optional[str] = None    # 한국어 게임명
    title_original: Optional[str] = None  # 원제 (영문/일문)
    description: Optional[str] = None     # 게임 시놉시스 / 설명
    description_korean: Optional[str] = None # 한국어 시놉시스
    release_date: Optional[str] = None    # 출시일 (YYYY-MM-DD 또는 YYYY)
    developer: Optional[str] = None       # 개발사
    publisher: Optional[str] = None       # 유통사
    genres: List[str] = field(default_factory=list)      # 장르 목록
    players: Optional[str] = None         # 플레이어 수 (예: "1-2 Players")
    rating: Optional[float] = None        # 평점 (100점 만점 기준)
    artwork: ScrapedArtwork = field(default_factory=ScrapedArtwork)
    raw_payload: Dict[str, Any] = field(default_factory=dict) # 원본 JSON 응답

    def to_dict(self) -> Dict[str, Any]:
        """JSON 변환용 dict 반환"""
        from dataclasses import asdict
        return asdict(self)
