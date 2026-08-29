# -*- coding: utf-8 -*-
"""
외부 메타데이터 프로바이더 패키지 (ScreenScraper, IGDB 등).
"""

from .base import ScrapedGameMetadata, ScrapedArtwork
from .screenscraper import ScreenScraperProvider, SC_SYSTEM_MAP
from .igdb import IGDBProvider, IGDB_PLATFORM_MAP
from .libretro import LibretroProvider, LIBRETRO_SYSTEM_REPOS

__all__ = [
    "ScrapedGameMetadata",
    "ScrapedArtwork",
    "ScreenScraperProvider",
    "SC_SYSTEM_MAP",
    "IGDBProvider",
    "IGDB_PLATFORM_MAP",
    "LibretroProvider",
    "LIBRETRO_SYSTEM_REPOS",
]
