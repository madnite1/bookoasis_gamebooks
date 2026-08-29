# -*- coding: utf-8 -*-
"""
IGDB (Internet Game Database / Twitch) REST API v4 연동 프로바이더.
OAuth 2.0 인증, 게임명 및 기종 ID 필터링 기반 메타데이터, 커버아트 및 스크린샷 수집.
"""

import time
import json
import urllib.request
import urllib.parse
import logging
from typing import Optional, Dict, Any, List

from ..models import RomAnalysisResult
from .base import ScrapedGameMetadata, ScrapedArtwork

logger = logging.getLogger(__name__)

# IGDB 기종별 고유 Platform ID 매핑
IGDB_PLATFORM_MAP: Dict[str, int] = {
    "n64": 4,
    "wii": 5,
    "psx": 7,
    "ps1": 7,
    "ps2": 8,
    "nes": 18,
    "snes": 19,
    "nds": 20,
    "gamecube": 21,
    "gbc": 22,
    "dreamcast": 23,
    "gba": 24,
    "megadrive": 29,
    "genesis": 29,
    "sega32x": 30,
    "saturn": 32,
    "gb": 33,
    "gamegear": 35,
    "3ds": 37,
    "psp": 38,
    "3do": 50,
    "fds": 51,
    "arcade": 52,
    "mame": 52,
    "fbneo": 52,
    "wonderswan": 57,
    "atari2600": 59,
    "atari7800": 60,
    "lynx": 61,
    "mastersystem": 64,
    "segacd": 78,
    "neogeo": 79,
    "pce": 86,
    "ngp": 119,
    "ngpc": 120,
    "wsc": 123,
    "pcecd": 128,
    "supergrafx": 150,
}


class IGDBProvider:
    """IGDB API v4 클라이언트"""

    AUTH_URL = "https://id.twitch.tv/oauth2/token"
    API_BASE = "https://api.igdb.com/v4"

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0

    def _get_token(self) -> Optional[str]:
        """Twitch OAuth 2.0 액세스 토큰 획득 및 갱신"""
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        if not self.client_id or not self.client_secret:
            return None

        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }).encode("utf-8")

        req = urllib.request.Request(self.AUTH_URL, data=params, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    self._access_token = data.get("access_token")
                    expires_in = data.get("expires_in", 3600)
                    self._token_expiry = time.time() + expires_in - 60
                    return self._access_token
        except Exception as e:
            logger.debug(f"IGDB token error: {e}")

        return None

    def fetch(self, rom_info: RomAnalysisResult) -> Optional[ScrapedGameMetadata]:
        """
        RomAnalysisResult로부터 IGDB 검색 및 메타데이터 수집
        """
        token = self._get_token()
        if not token:
            return None

        platform_id = IGDB_PLATFORM_MAP.get(rom_info.platform_slug) or IGDB_PLATFORM_MAP.get(rom_info.system_id)
        if not platform_id and rom_info.is_arcade:
            platform_id = 52

        # 검색 키워드 결정 (내부 헤더 타이틀 -> 파일명)
        query_title = rom_info.header_metadata.title or rom_info.file_name
        # 확장자 및 괄호 태그 정리
        clean_title = query_title.split("(")[0].split("[")[0].strip()

        # APICalypse 쿼리 구성
        fields = "id, name, summary, storyline, first_release_date, total_rating, cover.url, cover.image_id, screenshots.url, screenshots.image_id, genres.name, involved_companies.company.name, involved_companies.developer, involved_companies.publisher"
        where_clause = f"where platforms = ({platform_id});" if platform_id else ""
        query_body = f'search "{clean_title}"; fields {fields}; {where_clause} limit 5;'

        req = urllib.request.Request(
            f"{self.API_BASE}/games",
            data=query_body.encode("utf-8"),
            headers={
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    items = json.loads(resp.read().decode("utf-8"))
                    if items:
                        return self._parse_game(items[0])
        except Exception as e:
            logger.debug(f"IGDB query error for {clean_title}: {e}")

        return None

    def _parse_game(self, item: Dict[str, Any]) -> ScrapedGameMetadata:
        """IGDB JSON 아이템 파싱"""
        game_id = str(item.get("id", ""))
        title = item.get("name", "")
        summary = item.get("summary") or item.get("storyline")

        # 출시일 (유닉스 타임스탬프)
        release_date = None
        ts = item.get("first_release_date")
        if ts:
            try:
                release_date = time.strftime("%Y-%m-%d", time.gmtime(ts))
            except (TypeError, ValueError, OSError, OverflowError) as exc:
                logger.debug("IGDB release date parse failed for timestamp %r: %s", ts, exc)

        # 장르
        genres = [g.get("name") for g in item.get("genres", []) if g.get("name")]

        # 개발사 / 유통사
        developer = None
        publisher = None
        for comp_rel in item.get("involved_companies", []):
            c_name = comp_rel.get("company", {}).get("name")
            if comp_rel.get("developer") and not developer:
                developer = c_name
            if comp_rel.get("publisher") and not publisher:
                publisher = c_name

        # 평점
        rating = item.get("total_rating")

        # 미디어 / 커버아트 URL (고화질 t_cover_big 또는 t_1080p 변환)
        artwork = ScrapedArtwork()
        cover = item.get("cover", {})
        if cover.get("image_id"):
            img_id = cover.get("image_id")
            artwork.box_2d = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{img_id}.jpg"
        elif cover.get("url"):
            url = cover.get("url")
            if url.startswith("//"):
                url = f"https:{url}"
            artwork.box_2d = url.replace("t_thumb", "t_cover_big")

        screenshots = item.get("screenshots", [])
        if screenshots:
            ss = screenshots[0]
            if ss.get("image_id"):
                artwork.screenshot = f"https://images.igdb.com/igdb/image/upload/t_1080p/{ss.get('image_id')}.jpg"
            elif ss.get("url"):
                url = ss.get("url")
                if url.startswith("//"):
                    url = f"https:{url}"
                artwork.screenshot = url.replace("t_thumb", "t_1080p")

        return ScrapedGameMetadata(
            provider="igdb",
            game_id=game_id,
            title=title,
            title_original=title,
            description=summary,
            release_date=release_date,
            developer=developer,
            publisher=publisher,
            genres=genres,
            rating=rating,
            artwork=artwork,
            raw_payload=item
        )
