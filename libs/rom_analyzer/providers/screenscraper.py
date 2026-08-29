# -*- coding: utf-8 -*-
"""
ScreenScraper (screenscraper.fr) REST API 연동 프로바이더.
해시(CRC/MD5/SHA1), 시리얼 코드, 파일명 및 기종 ID 기반 정밀 게임 메타데이터 및 박스아트 수집.
"""

import urllib.request
import urllib.parse
import json
import logging
from typing import Optional, Dict, Any

from ..models import RomAnalysisResult
from .base import ScrapedGameMetadata, ScrapedArtwork

logger = logging.getLogger(__name__)

# ScreenScraper 기종별 고유 System ID 매핑
SC_SYSTEM_MAP: Dict[str, int] = {
    "megadrive": 1,
    "genesis": 1,
    "mastersystem": 2,
    "nes": 3,
    "snes": 4,
    "gb": 9,
    "gbc": 10,
    "gba": 12,
    "n64": 14,
    "nds": 15,
    "3ds": 17,
    "sega32x": 19,
    "segacd": 20,
    "gamegear": 21,
    "saturn": 22,
    "dreamcast": 23,
    "ngp": 25,
    "lynx": 28,
    "3do": 29,
    "pce": 31,
    "atari2600": 40,
    "atari7800": 42,
    "wonderswan": 45,
    "wsc": 46,
    "psx": 57,
    "ps1": 57,
    "ps2": 58,
    "gamecube": 59,
    "wii": 60,
    "psp": 61,
    "arcade": 75,
    "mame": 75,
    "fbneo": 75,
    "ngpc": 82,
    "supergrafx": 105,
    "fds": 106,
    "pcecd": 114,
    "neogeo": 142,
    "atomiswave": 53,
    "naomi": 56,
}


class ScreenScraperProvider:
    """ScreenScraper API 클라이언트"""

    API_BASE = "https://api.screenscraper.fr/api2/jeuInfos.php"

    def __init__(self, devid: str = "", devpassword: str = "", softname: str = "rom-analyzer", ssid: str = "", sspassword: str = ""):
        self.devid = devid
        self.devpassword = devpassword
        self.softname = softname
        self.ssid = ssid
        self.sspassword = sspassword

    def fetch(self, rom_info: RomAnalysisResult) -> Optional[ScrapedGameMetadata]:
        """
        RomAnalysisResult 객체를 전달받아 ScreenScraper에서 게임 메타데이터 조회
        """
        sys_id = SC_SYSTEM_MAP.get(rom_info.platform_slug) or SC_SYSTEM_MAP.get(rom_info.system_id)
        if not sys_id and rom_info.is_arcade:
            sys_id = 75

        params: Dict[str, Any] = {
            "devid": self.devid,
            "devpassword": self.devpassword,
            "softname": self.softname,
            "output": "json",
        }

        if self.ssid and self.sspassword:
            params["ssid"] = self.ssid
            params["sspassword"] = self.sspassword

        if sys_id:
            params["systemeid"] = str(sys_id)

        # 1. 파일 해시(CRC/MD5/SHA1) 파라미터 적용 (가장 정확)
        if rom_info.sha1:
            params["sha1"] = rom_info.sha1.lower()
        if rom_info.md5:
            params["md5"] = rom_info.md5.lower()
        if rom_info.crc32:
            params["crc"] = rom_info.crc32.lower()

        # 2. 파일명 및 크기
        params["romnom"] = rom_info.file_name
        if rom_info.file_size > 0:
            params["romtaille"] = str(rom_info.file_size)

        # 3. 디스크/카트리지 시리얼 번호가 있는 경우
        if rom_info.header_metadata.serial:
            params["serial"] = rom_info.header_metadata.serial

        query_url = f"{self.API_BASE}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(query_url, headers={"User-Agent": f"{self.softname}/1.0"})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return self._parse_response(data)
        except Exception as e:
            logger.debug(f"ScreenScraper fetch error for {rom_info.file_name}: {e}")

        return None

    def _parse_response(self, data: Dict[str, Any]) -> Optional[ScrapedGameMetadata]:
        """ScreenScraper JSON 응답 파싱"""
        response_obj = data.get("response", {})
        jeu = response_obj.get("jeu", {})
        if not jeu:
            return None

        game_id = str(jeu.get("id", ""))

        # 1. 다국어 타이틀 추출 (한국어 -> 영문 -> 기본)
        titles = jeu.get("noms", [])
        title_ko = None
        title_en = None
        title_default = jeu.get("nom", "")

        for n in titles:
            reg = n.get("region", "").lower()
            text = n.get("text", "")
            if reg in ["kr", "ko", "korea"] and not title_ko:
                title_ko = text
            elif reg in ["us", "en", "wor", "eu"] and not title_en:
                title_en = text

        final_title = title_ko or title_en or title_default

        # 2. 시놉시스 / 설명 추출
        synopses = jeu.get("synopsis", [])
        desc_ko = None
        desc_en = None
        for s in synopses:
            lang = s.get("langue", "").lower()
            text = s.get("text", "")
            if lang in ["kr", "ko"] and not desc_ko:
                desc_ko = text
            elif lang in ["en", "us"] and not desc_en:
                desc_en = text

        # 3. 개발사, 유통사, 출시일
        developer = jeu.get("developpeur", {}).get("text")
        publisher = jeu.get("editeur", {}).get("text")
        dates = jeu.get("dates", [])
        release_date = dates[0].get("text") if dates else None

        # 4. 장르 목록
        genres = []
        for g in jeu.get("genres", []):
            g_names = g.get("noms", [])
            for gn in g_names:
                if gn.get("langue", "").lower() in ["kr", "ko", "en"]:
                    genres.append(gn.get("text"))
                    break

        # 5. 미디어 / 아트웤 URL
        medias = jeu.get("medias", [])
        artwork = ScrapedArtwork()
        for m in medias:
            m_type = m.get("type", "").lower()
            m_url = m.get("url", "")
            if not m_url:
                continue

            if m_type in ["box-2d", "box-2d-front", "box-2d-side"]:
                if not artwork.box_2d:
                    artwork.box_2d = m_url
            elif m_type in ["box-3d"]:
                artwork.box_3d = m_url
            elif m_type in ["ss", "screenshot"]:
                if not artwork.screenshot:
                    artwork.screenshot = m_url
            elif m_type in ["sstitle", "titlescreen"]:
                artwork.title_screen = m_url
            elif m_type in ["wheel", "logo"]:
                artwork.wheel = m_url
            elif m_type in ["marquee"]:
                artwork.marquee = m_url
            elif m_type in ["fanart", "background"]:
                artwork.fanart = m_url

        # 평점 (20점 만점 -> 100점 환산)
        note = jeu.get("note", {}).get("text")
        rating = None
        try:
            if note:
                rating = float(note) * 5.0
        except (TypeError, ValueError) as exc:
            logger.debug("ScreenScraper rating parse failed for value %r: %s", note, exc)

        return ScrapedGameMetadata(
            provider="screenscraper",
            game_id=game_id,
            title=final_title,
            title_korean=title_ko,
            title_original=title_en or title_default,
            description=desc_ko or desc_en,
            description_korean=desc_ko,
            release_date=release_date,
            developer=developer,
            publisher=publisher,
            genres=genres,
            rating=rating,
            artwork=artwork,
            raw_payload=data
        )
