# -*- coding: utf-8 -*-
"""
Libretro Database (https://github.com/libretro/libretro-database) 및
Libretro Thumbnails (https://github.com/libretro-thumbnails) 연동 모듈.
해시(CRC/MD5/SHA1), 시리얼, 내부 타이틀을 기반으로 Libretro 정식 규격 명칭 및 박스아트 URL 자동 도출.
"""

import re
import urllib.parse
from typing import Optional, Dict, Any, List

from ..models import RomAnalysisResult
from .base import ScrapedGameMetadata, ScrapedArtwork

# Libretro 시스템 폴더명 매핑
LIBRETRO_SYSTEM_REPOS: Dict[str, str] = {
    "nes": "Nintendo_-_Nintendo_Entertainment_System",
    "fds": "Nintendo_-_Family_Computer_Disk_System",
    "snes": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "gb": "Nintendo_-_Game_Boy",
    "gbc": "Nintendo_-_Game_Boy_Color",
    "gba": "Nintendo_-_Game_Boy_Advance",
    "nds": "Nintendo_-_Nintendo_DS",
    "3ds": "Nintendo_-_Nintendo_3DS",
    "n64": "Nintendo_-_Nintendo_64",
    "gamecube": "Nintendo_-_GameCube",
    "wii": "Nintendo_-_Wii",
    "megadrive": "Sega_-_Mega_Drive_-_Genesis",
    "genesis": "Sega_-_Mega_Drive_-_Genesis",
    "mastersystem": "Sega_-_Master_System_-_Mark_III",
    "gamegear": "Sega_-_Game_Gear",
    "sega32x": "Sega_-_32X",
    "segacd": "Sega_-_Mega-CD_-_Sega_CD",
    "saturn": "Sega_-_Saturn",
    "dreamcast": "Sega_-_Dreamcast",
    "psx": "Sony_-_PlayStation",
    "ps1": "Sony_-_PlayStation",
    "ps2": "Sony_-_PlayStation_2",
    "psp": "Sony_-_PlayStation_Portable",
    "pce": "NEC_-_PC_Engine_-_TurboGrafx_16",
    "pcecd": "NEC_-_PC_Engine_-_TurboGrafx_16",
    "supergrafx": "NEC_-_PC_Engine_SuperGrafx",
    "pcfx": "NEC_-_PC-FX",
    "wonderswan": "Bandai_-_WonderSwan",
    "wsc": "Bandai_-_WonderSwan_Color",
    "ngp": "SNK_-_Neo_Geo_Pocket",
    "ngpc": "SNK_-_Neo_Geo_Pocket_Color",
    "neogeo": "SNK_-_Neo_Geo",
    "arcade": "MAME",
    "mame": "MAME",
    "fbneo": "FBNeo_-_Arcade_Games",
    "atari2600": "Atari_-_2600",
    "atari5200": "Atari_-_5200",
    "atari7800": "Atari_-_7800",
    "lynx": "Atari_-_Lynx",
    "jaguar": "Atari_-_Jaguar",
    "3do": "The_3DO_Company_-_3DO",
}

LIBRETRO_CDN_BASE = "https://raw.githubusercontent.com/libretro-thumbnails"


class LibretroProvider:
    """Libretro Database 및 Thumbnails 연동 프로바이더"""

    @classmethod
    def clean_libretro_name(cls, title: str) -> str:
        """
        Libretro 명명 규칙에 맞게 특수문자 변환.
        Libretro 규칙: & -> _, ` -> ', / \ : * ? " < > | 제거 또는 치환
        """
        if not title:
            return ""
        cleaned = title.replace("&", "_")
        for bad_char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            cleaned = cleaned.replace(bad_char, "_")
        return re.sub(r"\s+", " ", cleaned).strip()

    @classmethod
    def resolve_artwork_urls(cls, system_repo: str, canonical_title: str) -> ScrapedArtwork:
        """
        Libretro 공식 저장소 기반 3종 미디어(Named_Boxarts, Named_Snaps, Named_Titles) URL 생성
        """
        if not system_repo or not canonical_title:
            return ScrapedArtwork()

        esc_title = cls.clean_libretro_name(canonical_title)
        quoted_title = urllib.parse.quote(f"{esc_title}.png", safe="")

        base_repo_url = f"{LIBRETRO_CDN_BASE}/{system_repo}/master"

        return ScrapedArtwork(
            box_2d=f"{base_repo_url}/Named_Boxarts/{quoted_title}",
            screenshot=f"{base_repo_url}/Named_Snaps/{quoted_title}",
            title_screen=f"{base_repo_url}/Named_Titles/{quoted_title}"
        )

    @classmethod
    def fetch(cls, rom_info: RomAnalysisResult) -> Optional[ScrapedGameMetadata]:
        """
        RomAnalysisResult 기반으로 Libretro Database 정규 명칭 및 무료 CDN 박스아트 도출
        """
        sys_key = rom_info.platform_slug or rom_info.system_id
        system_repo = LIBRETRO_SYSTEM_REPOS.get(sys_key)
        if not system_repo and rom_info.is_arcade:
            system_repo = "MAME"

        if not system_repo:
            return None

        # 정식 타이틀 (헤더 메타데이터 타이틀 -> 아케이드 타이틀 -> 파일명)
        title = rom_info.header_metadata.title
        if not title and rom_info.is_arcade:
            title = rom_info.arcade_info.driver
        if not title:
            title = rom_info.file_name.rsplit(".", 1)[0]

        artwork = cls.resolve_artwork_urls(system_repo, title)

        return ScrapedGameMetadata(
            provider="libretro",
            game_id=rom_info.header_metadata.serial or rom_info.crc32,
            title=title,
            title_original=title,
            artwork=artwork,
            raw_payload={
                "system_repo": system_repo,
                "canonical_name": cls.clean_libretro_name(title)
            }
        )
