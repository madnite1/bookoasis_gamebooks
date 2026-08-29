# -*- coding: utf-8 -*-
"""
ROM Analyzer - Retro Game ROM Identification & Inspection Package.
재사용 가능한 ROM 정밀 기종, 기판, 바이오스, 디스크 구조 식별 및 검사 모듈.
"""

from .analyzer import analyze, analyzer, RomAnalyzer
from .models import RomAnalysisResult, ArcadeInfo, ArcadeCoreCompatibility, DiscInfo, DiscEntryInfo, BiosInfo, HeaderMetadata, DetectionEvidence, EmulatorJsInfo
from .arcade.compatibility import ArcadeCompatibilityManager
from .evidence import EvidenceScorer
from .core_info import CoreInfoManager, CoreInfo, CoreFirmwareRequirement, LIBRETRO_CORES_CATALOG, EMULATORJS_STABLE_CORES, EMULATORJS_STABLE_VERSION, EJS_SYSTEM_ALIAS_BY_ROM_SYSTEM
from .providers import ScreenScraperProvider, IGDBProvider, LibretroProvider, ScrapedGameMetadata, ScrapedArtwork

__version__ = "1.1.0"
__all__ = [
    "analyze",
    "analyzer",
    "RomAnalyzer",
    "RomAnalysisResult",
    "ArcadeInfo",
    "ArcadeCoreCompatibility",
    "ArcadeCompatibilityManager",
    "DiscInfo",
    "DiscEntryInfo",
    "BiosInfo",
    "HeaderMetadata",
    "DetectionEvidence",
    "EmulatorJsInfo",
    "EvidenceScorer",
    "CoreInfoManager",
    "CoreInfo",
    "CoreFirmwareRequirement",
    "LIBRETRO_CORES_CATALOG",
    "EMULATORJS_STABLE_CORES",
    "EMULATORJS_STABLE_VERSION",
    "EJS_SYSTEM_ALIAS_BY_ROM_SYSTEM",
    "ScreenScraperProvider",
    "IGDBProvider",
    "LibretroProvider",
    "ScrapedGameMetadata",
    "ScrapedArtwork",
]
