# -*- coding: utf-8 -*-
"""
북오아시스 EmulatorJS 레트로 게임 에뮬레이터 플러그인.
웹어셈블리(WebAssembly) 기반 EmulatorJS 게임 에뮬레이터 플레이어,
ROM 라이브러리 관리자 및 유저별 영속 클라우드 세이브(배터리 세이브 + 실시간 스냅샷) 동기화 플러그인.
"""

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

from plugins.metadata.base import BaseMetadataProvider

# 플러그인 전용 격리 패키지(libs/) sys.path 등록
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBS_DIR = os.path.join(_PLUGIN_DIR, "libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

logger = logging.getLogger(__name__)

SELF_ID = "bookoasis_gamebooks"
ROUTE_BASE = f"/api/webhook/{SELF_ID}"

_DB_LOCK = threading.Lock()
_ROUTES_LOCK = threading.Lock()
_REGISTERED_APPS = set()

KST = timezone(timedelta(hours=9))


def _get_kst_now_str():
    """한국 표준시(KST, UTC+9) 기준 현재 시간 포맷 문자열 반환"""
    return datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


_ROMM_NAME_PREFIX_RE = re.compile(r"^romm[_\s\-]+\d+[_\s\-]+", re.I)


def _strip_romm_name_prefix(name):
    """romm_194_cutefght / romm 194 cutefght → cutefght"""
    text = str(name or "").strip()
    if not text:
        return ""
    base = os.path.splitext(os.path.basename(text.replace("\\", "/")))[0]
    cleaned = _ROMM_NAME_PREFIX_RE.sub("", base, count=1).strip(" ._")
    return cleaned


# Homebrew Hub (https://hh.gbdev.io) — GB/GBC/GBA/NES 합법 무료 홈브류·데모 카탈로그
HH_API = "https://hh3.gbdev.io/api"
HH_USER_AGENT = "BookOasis-GameBooks/1.2 (legal homebrew catalog; +https://hh.gbdev.io)"
HH_ALLOWED_TYPTAGS = {"game", "homebrew", "demo"}
HH_ALLOWED_PLATFORMS = {"GB", "GBC", "GBA", "NES"}
HH_ALLOWED_BASEREPO = {
    "https://github.com/gbdev/database",
    "https://github.com/gbadev-org/games",
    "https://github.com/nesdev-org/homebrew-db",
}
HH_MAX_ROM_BYTES = 32 * 1024 * 1024
HH_PLAYABLE_EXTS = {".gb", ".gbc", ".gba", ".nes"}


def _hh_http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": HH_USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def _hh_safe_relpath(name):
    rel = str(name or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    return rel


def _hh_github_raw_base(baserepo):
    repo = str(baserepo or "").rstrip("/")
    if repo not in HH_ALLOWED_BASEREPO:
        return None
    path = repo.split("github.com/", 1)[-1]
    return f"https://raw.githubusercontent.com/{path}/master"


def _hh_playable_file(entry):
    files = entry.get("files") or []
    preferred = None
    for item in files:
        if not isinstance(item, dict):
            continue
        rel = _hh_safe_relpath(item.get("filename"))
        if not rel:
            continue
        ext = os.path.splitext(rel)[1].lower()
        if ext not in HH_PLAYABLE_EXTS:
            continue
        if item.get("playable") is False:
            continue
        if item.get("default"):
            return rel
        if preferred is None:
            preferred = rel
    return preferred


def _hh_entry_allowed(entry):
    if not isinstance(entry, dict):
        return False
    typetag = str(entry.get("typetag") or "").strip().lower()
    platform = str(entry.get("platform") or "").strip().upper()
    if typetag not in HH_ALLOWED_TYPTAGS:
        return False
    if platform not in HH_ALLOWED_PLATFORMS:
        return False
    if not _hh_github_raw_base(entry.get("baserepo")):
        return False
    return _hh_playable_file(entry) is not None


# Libretro Thumbnails CDN (https://github.com/libretro-thumbnails/libretro-thumbnails)
LIBRETRO_CDN_BASE = "https://raw.githubusercontent.com/libretro-thumbnails"

LIBRETRO_SYSTEM_MAP = {
    "gba": "Nintendo_-_Game_Boy_Advance",
    "gb": "Nintendo_-_Game_Boy",
    "gbc": "Nintendo_-_Game_Boy_Color",
    "snes": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "nes": "Nintendo_-_Nintendo_Entertainment_System",
    "fds": "Nintendo_-_Family_Computer_Disk_System",
    "nds": "Nintendo_-_Nintendo_DS",
    "n64": "Nintendo_-_Nintendo_64",
    "vb": "Nintendo_-_Virtual_Boy",
    "virtualboy": "Nintendo_-_Virtual_Boy",
    "segamd": "Sega_-_Mega_Drive_-_Genesis",
    "genesis": "Sega_-_Mega_Drive_-_Genesis",
    "segams": "Sega_-_Master_System_-_Mark_III",
    "mastersystem": "Sega_-_Master_System_-_Mark_III",
    "segagg": "Sega_-_Game_Gear",
    "gamegear": "Sega_-_Game_Gear",
    "sega32x": "Sega_-_32X",
    "segacd": "Sega_-_Mega-CD_-_Sega_CD",
    "saturn": "Sega_-_Saturn",
    "segasaturn": "Sega_-_Saturn",
    "psx": "Sony_-_PlayStation",
    "ps1": "Sony_-_PlayStation",
    "psp": "Sony_-_PlayStation_Portable",
    "pce": "NEC_-_PC_Engine_-_TurboGrafx_16",
    "pcfx": "NEC_-_PC-FX",
    "supergrafx": "NEC_-_PC_Engine_SuperGrafx",
    "ngp": "SNK_-_Neo_Geo_Pocket",
    "ngpc": "SNK_-_Neo_Geo_Pocket_Color",
    "neogeo": "SNK_-_Neo_Geo",
    "neo-geo": "SNK_-_Neo_Geo",
    "wonderswan": "Bandai_-_WonderSwan",
    "wsc": "Bandai_-_WonderSwan_Color",
    "wonderswancolor": "Bandai_-_WonderSwan_Color",
    "atari2600": "Atari_-_2600",
    "atari5200": "Atari_-_5200",
    "atari7800": "Atari_-_7800",
    "lynx": "Atari_-_Lynx",
    "jaguar": "Atari_-_Jaguar",
    "coleco": "Coleco_-_ColecoVision",
    "amiga": "Commodore_-_Amiga",
    "c64": "Commodore_-_64",
    "arcade": "MAME",
}


_LIBRETRO_REPO_TITLES_CACHE = {}
_LIBRETRO_REPO_CACHE_TIME = {}


def _get_libretro_repo_titles(repo):
    """Libretro 레포의 Named_Boxarts 목록을 캐싱하여 가져옵니다."""
    now = time.time()
    if repo in _LIBRETRO_REPO_TITLES_CACHE and (now - _LIBRETRO_REPO_CACHE_TIME.get(repo, 0) < 86400):
        return _LIBRETRO_REPO_TITLES_CACHE[repo]

    url = f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master?recursive=1"
    req = urllib.request.Request(url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tree = data.get("tree", [])
            titles = []
            for it in tree:
                p = it.get("path", "")
                if p.startswith("Named_Boxarts/") and p.endswith(".png"):
                    fname = os.path.basename(p)
                    titles.append(os.path.splitext(fname)[0])
            if titles:
                _LIBRETRO_REPO_TITLES_CACHE[repo] = titles
                _LIBRETRO_REPO_CACHE_TIME[repo] = now
                return titles
    except Exception as e:
        logger.debug(f"[{SELF_ID}] Libretro tree fetch error ({repo}): {e}")

    return _LIBRETRO_REPO_TITLES_CACHE.get(repo, [])


def _clean_libretro_name(name):
    """Libretro Thumbnails 규칙에 맞는 게임명 정리 (특수문자 매핑)"""
    # Libretro 특수문자 규칙: & -> _, ` -> ', 등
    # 원본 파일명에서 확장자 및 접두사 정리
    base = os.path.splitext(os.path.basename(name.replace("\\", "/")))[0]
    base = _ROMM_NAME_PREFIX_RE.sub("", base, count=1).strip(" ._")
    # Libretro naming convention: replace & with _
    base_escaped = base.replace("&", "_")
    return base, base_escaped


def _fetch_libretro_artwork(platform_or_core, filename, raw_title=""):
    """Libretro Thumbnails CDN에서 박스아트 탐색 후 바이트 데이터 반환"""
    key = str(platform_or_core or "").lower().strip()
    system_repo = LIBRETRO_SYSTEM_MAP.get(key)
    if not system_repo:
        # Fallback direct lookup
        for k, v in LIBRETRO_SYSTEM_MAP.items():
            if k in key:
                system_repo = v
                break
    if not system_repo:
        return None

    candidates = []
    base_orig, base_esc = _clean_libretro_name(filename)
    candidates.append(base_orig)
    if base_esc != base_orig:
        candidates.append(base_esc)

    if raw_title and raw_title != base_orig:
        t_orig, t_esc = _clean_libretro_name(raw_title)
        candidates.append(t_orig)
        if t_esc != t_orig:
            candidates.append(t_esc)

    # 괄호 태그 제거 버전도 시도
    no_tag = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_orig).strip()
    if no_tag and no_tag not in candidates:
        candidates.append(no_tag)

    # 지역 태그((USA), (Japan), (Europe)) 조합 자동 시도 (Libretro 파일명 규칙)
    expanded = []
    for c in list(candidates):
        expanded.append(c)
        if not re.search(r"\((USA|Japan|Europe|World|Korea)\)", c, re.I):
            expanded.append(f"{c} (USA)")
            expanded.append(f"{c} (Japan)")
            expanded.append(f"{c} (Europe)")
            expanded.append(f"{c} (World)")
    candidates = expanded

    seen = set()
    for name_cand in candidates:
        if not name_cand or name_cand in seen:
            continue
        seen.add(name_cand)

        # Named_Boxarts 탐색
        encoded_name = urllib.parse.quote(f"{name_cand}.png", safe="")
        url = f"{LIBRETRO_CDN_BASE}/{system_repo}/master/Named_Boxarts/{encoded_name}"
        try:
            curr_url = url
            for _ in range(3):
                req = urllib.request.Request(curr_url, headers={"User-Agent": HH_USER_AGENT})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        data = resp.read()
                        if len(data) < 256 and b".png" in data:
                            target_filename = data.decode("utf-8", errors="ignore").strip()
                            base_url_dir = curr_url.rsplit("/", 1)[0]
                            curr_url = f"{base_url_dir}/{urllib.parse.quote(target_filename)}"
                            continue
                        if data and len(data) > 512:
                            return data
                break
        except Exception:
            pass

    # 직접 URL 히트 실패 시, 레포 전체 타이틀 목록 캐시를 활용한 스마트 퍼지 검색 (1차 일치 항목 자동 다운로드)
    try:
        repo_titles = _get_libretro_repo_titles(system_repo)
        if repo_titles:
            search_terms = []
            if raw_title:
                search_terms.append(raw_title)
            search_terms.append(base_orig)
            if no_tag and no_tag not in search_terms:
                search_terms.append(no_tag)

            stopwords = {"64", "v64", "z64", "n64", "k", "j", "u", "e", "the", "of", "and", "in", "to", "ad", "rpg", "act"}
            best_match = None
            for term in search_terms:
                kws = [w.lower() for w in re.sub(r"[^a-zA-Z0-9\s]", " ", term).split() if len(w) >= 2]
                meaningful = [w for w in kws if w not in stopwords]
                if not meaningful:
                    continue

                for rt in repo_titles:
                    rt_lower = rt.lower()
                    if all(m in rt_lower for m in meaningful):
                        best_match = rt
                        break
                if best_match:
                    break

            if best_match:
                enc_match = urllib.parse.quote(f"{best_match}.png", safe="")
                match_url = f"{LIBRETRO_CDN_BASE}/{system_repo}/master/Named_Boxarts/{enc_match}"
                curr_url = match_url
                for _ in range(3):
                    req = urllib.request.Request(curr_url, headers={"User-Agent": HH_USER_AGENT})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status == 200:
                            data = resp.read()
                            if len(data) < 256 and b".png" in data:
                                target_filename = data.decode("utf-8", errors="ignore").strip()
                                base_url_dir = curr_url.rsplit("/", 1)[0]
                                curr_url = f"{base_url_dir}/{urllib.parse.quote(target_filename)}"
                                continue
                            if data and len(data) > 512:
                                return data
                    break
    except Exception as e:
        logger.debug(f"[{SELF_ID}] Libretro fuzzy match error: {e}")

    return None


# ScreenScraper 플랫폼 ID 매핑
SS_SYSTEM_MAP = {
    "gba": "12",
    "gb": "9",
    "gbc": "10",
    "snes": "4",
    "nes": "3",
    "fds": "106",
    "nds": "15",
    "n64": "14",
    "vb": "11",
    "virtualboy": "11",
    "segamd": "1",
    "genesis": "1",
    "segams": "2",
    "mastersystem": "2",
    "segagg": "21",
    "gamegear": "21",
    "sega32x": "19",
    "segacd": "20",
    "saturn": "22",
    "segasaturn": "22",
    "psx": "57",
    "ps1": "57",
    "psp": "61",
    "pce": "31",
    "pcfx": "32",
    "supergrafx": "105",
    "ngp": "25",
    "ngpc": "82",
    "neogeo": "142",
    "neo-geo": "142",
    "wonderswan": "45",
    "wsc": "46",
    "wonderswancolor": "46",
    "atari2600": "40",
    "atari5200": "41",
    "atari7800": "42",
    "lynx": "28",
    "jaguar": "27",
    "coleco": "48",
    "amiga": "64",
    "c64": "66",
    "arcade": "75",
}


def _calc_crc32(file_path):
    """파일 CRC32 계산"""
    try:
        import binascii
        crc = 0
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                crc = binascii.crc32(chunk, crc)
        return f"{crc & 0xFFFFFFFF:08x}".upper()
    except Exception:
        return None


def _calc_md5(file_path):
    """파일 MD5 계산"""
    try:
        m = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                m.update(chunk)
        return m.hexdigest().lower()
    except Exception:
        return None


def _fetch_screenscraper_artwork(file_path, platform_or_core, filename, sc_config):
    """ScreenScraper API를 질의하여 롬 아트워크 다운로드 (Key 설정 시에만 동작)"""
    devid = sc_config.get("ss_devid")
    devpassword = sc_config.get("ss_devpassword")
    if not devid or not devpassword:
        return None

    if not file_path or not os.path.exists(file_path):
        return None

    crc = _calc_crc32(file_path)
    md5_val = _calc_md5(file_path)
    file_size = os.path.getsize(file_path)
    plat_key = str(platform_or_core or "").lower().strip()
    system_id = SS_SYSTEM_MAP.get(plat_key, "")

    base_url = "https://www.screenscraper.fr/api2/jeuInfos.php"
    params = {
        "devid": devid,
        "devpassword": devpassword,
        "softname": "BookOasis",
        "output": "json",
        "romnom": filename,
        "romtaille": str(file_size),
    }
    if crc:
        params["crc"] = crc
    if md5_val:
        params["md5"] = md5_val
    if system_id:
        params["systemeid"] = system_id

    user = sc_config.get("ss_user")
    pwd = sc_config.get("ss_password")
    if user and pwd:
        params["ssid"] = user
        params["sspassword"] = pwd

    try:
        query_str = urllib.parse.urlencode(params)
        req_url = f"{base_url}?{query_str}"
        req = urllib.request.Request(req_url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                medias = data.get("response", {}).get("jeu", {}).get("medias", [])
                for m in medias:
                    # 2d boxart 또는 3d boxart 또는 screenshot 탐색
                    mtype = str(m.get("type") or "").lower()
                    if mtype in ("box-2d", "box-3d", "wheel", "screenshot"):
                        img_url = m.get("url")
                        if img_url:
                            img_req = urllib.request.Request(img_url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
                            with urllib.request.urlopen(img_req, timeout=15) as img_resp:
                                if img_resp.status == 200:
                                    img_data = img_resp.read()
                                    if img_data and len(img_data) > 512:
                                        return img_data
    except Exception as e:
        logger.debug(f"[{SELF_ID}] ScreenScraper query error: {e}")

    return None


_IGDB_ACCESS_TOKEN = None
_IGDB_TOKEN_EXPIRY = 0


def _get_igdb_token(client_id, client_secret):
    """Twitch OAuth 토큰 발급/캐싱"""
    global _IGDB_ACCESS_TOKEN, _IGDB_TOKEN_EXPIRY
    now = time.time()
    if _IGDB_ACCESS_TOKEN and now < _IGDB_TOKEN_EXPIRY - 60:
        return _IGDB_ACCESS_TOKEN

    token_url = "https://id.twitch.tv/oauth2/token"
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(token_url, data=params, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                _IGDB_ACCESS_TOKEN = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                _IGDB_TOKEN_EXPIRY = now + expires_in
                return _IGDB_ACCESS_TOKEN
    except Exception as e:
        logger.debug(f"[{SELF_ID}] IGDB token error: {e}")

    return None


def _fetch_igdb_artwork(raw_title, igdb_config):
    """IGDB API를 질의하여 고화질 커버 아트 다운로드 (Key 설정 시에만 동작)"""
    client_id = igdb_config.get("igdb_client_id")
    client_secret = igdb_config.get("igdb_client_secret")
    if not client_id or not client_secret or not raw_title:
        return None

    token = _get_igdb_token(client_id, client_secret)
    if not token:
        return None

    clean_t = re.sub(r"[\(\[\{].*?[\)\]\}]", "", raw_title).strip()
    if not clean_t:
        clean_t = raw_title

    # IGDB Apicalypse 쿼리
    escaped_title = clean_t.replace('"', '\\"')
    query_body = f'search "{escaped_title}"; fields name, cover.image_id, cover.url; limit 1;'

    try:
        req = urllib.request.Request(
            "https://api.igdb.com/v4/games",
            data=query_body.encode("utf-8"),
            headers={
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                results = json.loads(resp.read().decode("utf-8"))
                if results and isinstance(results, list):
                    cover = results[0].get("cover")
                    if cover:
                        img_id = cover.get("image_id")
                        if img_id:
                            img_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{img_id}.jpg"
                        else:
                            raw_url = cover.get("url", "")
                            img_url = ("https:" + raw_url) if raw_url.startswith("//") else raw_url

                        if img_url:
                            img_req = urllib.request.Request(img_url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
                            with urllib.request.urlopen(img_req, timeout=12) as img_resp:
                                if img_resp.status == 200:
                                    img_data = img_resp.read()
                                    if img_data and len(img_data) > 512:
                                        return img_data
    except Exception as e:
        logger.debug(f"[{SELF_ID}] IGDB query error: {e}")

    return None


def _sanitize_id(text):
    """안전한 고유 ID 생성 (특수문자 제거 및 소문자 해시 결합)."""
    clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", text)
    h = hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{clean[:40]}_{h}"


def _get_current_user_id():
    """현재 세션의 user_id 추출 (Flask 세션 우선)"""
    try:
        from flask import request, session

        # 1. Flask 세션 우선 확인 (로그인된 실제 유저 계정)
        uid = session.get("user_id")
        if not uid:
            user_dict = session.get("user")
            if isinstance(user_dict, dict):
                uid = user_dict.get("id")

        if uid:
            return int(uid)

        # 2. 쿼리 파라미터 확인 (세션이 없는 API 독립 호출 대비)
        uid_arg = request.args.get("user_id")
        if uid_arg:
            try:
                return int(uid_arg)
            except Exception:
                pass
    except Exception:
        pass
    return 1


def _is_current_user_admin():
    """현재 세션 유저가 관리자(admin) 권한을 가지고 있는지 검사합니다."""
    try:
        from flask import session
        if session.get("role") == "admin":
            return True
        user_dict = session.get("user")
        if isinstance(user_dict, dict) and user_dict.get("role") == "admin":
            return True
        if session.get("is_admin"):
            return True

        uid = _get_current_user_id()
        if uid:
            from repositories.user_repository import UserRepository
            user = UserRepository.find_by_id("general", uid)
            if user and user.get("role") == "admin":
                return True
    except Exception as e:
        logger.debug(f"[{SELF_ID}] Admin check error: {e}")
    return False


SUPPORTED_SYSTEMS = {
    # 닌텐도 (Nintendo)
    ".gba": {"core": "gba", "platform": "GBA", "name": "Game Boy Advance"},
    ".gb": {"core": "gb", "platform": "GB", "name": "Game Boy"},
    ".gbc": {"core": "gbc", "platform": "GBC", "name": "Game Boy Color"},
    ".sfc": {"core": "snes", "platform": "SNES", "name": "Super Famicom"},
    ".smc": {"core": "snes", "platform": "SNES", "name": "Super Famicom"},
    ".snes": {"core": "snes", "platform": "SNES", "name": "Super Famicom"},
    ".fig": {"core": "snes", "platform": "SNES", "name": "Super Famicom"},
    ".nes": {"core": "nes", "platform": "NES", "name": "Famicom / NES"},
    ".fds": {"core": "nes", "platform": "FDS", "name": "Famicom Disk System"},
    ".unf": {"core": "nes", "platform": "NES", "name": "Famicom / NES"},
    ".unif": {"core": "nes", "platform": "NES", "name": "Famicom / NES"},
    ".nds": {"core": "nds", "platform": "NDS", "name": "Nintendo DS"},
    ".n64": {"core": "n64", "platform": "N64", "name": "Nintendo 64"},
    ".z64": {"core": "n64", "platform": "N64", "name": "Nintendo 64"},
    ".v64": {"core": "n64", "platform": "N64", "name": "Nintendo 64"},
    ".vb": {"core": "vb", "platform": "VirtualBoy", "name": "Virtual Boy"},
    ".vboy": {"core": "vb", "platform": "VirtualBoy", "name": "Virtual Boy"},

    # 세가 (SEGA)
    ".md": {"core": "segaMD", "platform": "Genesis", "name": "Mega Drive"},
    ".gen": {"core": "segaMD", "platform": "Genesis", "name": "Mega Drive"},
    ".smd": {"core": "segaMD", "platform": "Genesis", "name": "Mega Drive"},
    ".sms": {"core": "segaMS", "platform": "MasterSystem", "name": "Master System"},
    ".gg": {"core": "segaGG", "platform": "GameGear", "name": "Game Gear"},
    ".32x": {"core": "sega32x", "platform": "Sega32X", "name": "Sega 32X"},
    ".sg": {"core": "segaMS", "platform": "SG-1000", "name": "SG-1000"},

    # 소니 (Sony)
    ".psx": {"core": "psx", "platform": "PS1", "name": "PlayStation 1"},
    ".ps1": {"core": "psx", "platform": "PS1", "name": "PlayStation 1"},
    ".pbp": {"core": "psx", "platform": "PS1", "name": "PlayStation 1"},
    ".cso": {"core": "psp", "platform": "PSP", "name": "PlayStation Portable"},

    # 아케이드 / NEC / SNK / 아타리 / 반다이 등
    ".pce": {"core": "pce", "platform": "PCE", "name": "PC Engine"},
    ".sgx": {"core": "pce", "platform": "SuperGrafx", "name": "PC Engine SuperGrafx"},
    ".pcfx": {"core": "pcfx", "platform": "PC-FX", "name": "PC-FX"},
    ".ngp": {"core": "ngp", "platform": "NGP", "name": "Neo Geo Pocket"},
    ".ngc": {"core": "ngp", "platform": "NGPC", "name": "Neo Geo Pocket Color"},
    ".ws": {"core": "ws", "platform": "WonderSwan", "name": "WonderSwan"},
    ".wsc": {"core": "ws", "platform": "WonderSwanColor", "name": "WonderSwan Color"},
    ".a26": {"core": "atari2600", "platform": "Atari2600", "name": "Atari 2600"},
    ".a52": {"core": "atari5200", "platform": "Atari5200", "name": "Atari 5200"},
    ".a78": {"core": "atari7800", "platform": "Atari7800", "name": "Atari 7800"},
    ".lnx": {"core": "lynx", "platform": "Lynx", "name": "Atari Lynx"},
    ".j64": {"core": "jaguar", "platform": "Jaguar", "name": "Atari Jaguar"},
    ".jag": {"core": "jaguar", "platform": "Jaguar", "name": "Atari Jaguar"},
    ".col": {"core": "coleco", "platform": "ColecoVision", "name": "ColecoVision"},
    ".adf": {"core": "amiga", "platform": "Amiga", "name": "Commodore Amiga"},
    ".d64": {"core": "c64", "platform": "C64", "name": "Commodore 64"},
}

KNOWN_BIOS_STEMS = {
    "22vp931", "3dobios", "acpsx", "airlbios", "aleck64", "alg_bios", "allied", "ar_bios",
    "aristmk5", "aristmk6", "atarisy1", "atluspsx", "atpsx", "awbios", "bios_cd_e", "bios_cd_j",
    "bios_cd_u", "bsmt2000", "bubsys", "cd32bios", "cdibios", "chihiro", "cpzn1", "cpzn2",
    "crysbios", "decocass", "disksys", "f355bios", "galgbios", "gba_bios", "gp_110", "gq863",
    "gts1", "gts1s", "hikaru", "hng64", "hod2bios", "isgsm", "iteagle", "k573dio", "k573mcr",
    "k573msu", "k573npu", "konamigv", "konamigx", "kviper", "ldv1000", "m50458", "macsbios",
    "maxaflex", "megaplay", "megatech", "midssio", "mie", "mk6nsw11", "namco50", "namco51",
    "namco52", "namco53", "namco54", "namco62", "naomi", "naomi2", "naomigd", "neogeo",
    "nss", "pgm", "playch10", "pr8210", "psarc95", "pyson", "qsound", "saa5050", "sammymdl",
    "scph1001", "scph5500", "scph5501", "scph5502", "scph7001", "sfcbox", "shtzone", "simutrek",
    "skns", "stvbios", "su2000", "sys246", "sys256", "sys573", "syscard3", "taitofx1",
    "taitogn", "taitotz", "tms32031", "tms32032", "tourvis", "tps", "triforce", "v4bios",
    "vspsx", "ym2608"
}

KNOWN_ARCADE_TITLES = {
    "wof": "천지를 먹다 2 (Warriors of Fate)",
    "wofj": "천지를 먹다 2 (천지식을먹다 2)",
    "wofa": "천지를 먹다 2 (아시아판)",
    "wofu": "천지를 먹다 2 (북미판)",
    "captcomm": "캡틴 코만도 (Captain Commando)",
    "captcommj": "캡틴 코만도 (일판)",
    "dino": "캐딜락 & 다이노소어 (Cadillacs & Dinosaurs)",
    "dinoj": "캐딜락 & 다이노소어 (일판)",
    "ffight": "파이널 파이트 (Final Fight)",
    "ffightj": "파이널 파이트 (일판)",
    "punisher": "퍼니셔 (The Punisher)",
    "punisherj": "퍼니셔 (일판)",
    "avsp": "에이리언 vs 프레데터 (Alien vs Predator)",
    "ddsom": "던전 & 드래곤: 섀도 오버 미스타라",
    "ddtod": "던전 & 드래곤: 타워 오브 둠",
    "kod": "원탁의 기사 (Knights of the Round)",
    "sf2": "스트리트 파이터 2",
    "sf2ce": "스트리트 파이터 2 챔피언 에디션",
    "sf2hf": "스트리트 파이터 2 하이퍼 파이팅",
    "ssf2": "슈퍼 스트리트 파이터 2",
    "ssf2t": "슈퍼 스트리트 파이터 2 터보",
    "sfa": "스트리트 파이터 제로 (Alpha)",
    "sfa2": "스트리트 파이터 제로 2 (Alpha 2)",
    "sfa3": "스트리트 파이터 제로 3 (Alpha 3)",
    "kof94": "더 킹 오브 파이터즈 94",
    "kof95": "더 킹 오브 파이터즈 95",
    "kof96": "더 킹 오브 파이터즈 96",
    "kof97": "더 킹 오브 파이터즈 97",
    "kof98": "더 킹 오브 파이터즈 98",
    "kof99": "더 킹 오브 파이터즈 99",
    "kof2000": "더 킹 오브 파이터즈 2000",
    "kof2001": "더 킹 오브 파이터즈 2001",
    "kof2002": "더 킹 오브 파이터즈 2002",
    "kof2003": "더 킹 오브 파이터즈 2003",
    "mslug": "메탈슬러그",
    "mslug2": "메탈슬러그 2",
    "mslugx": "메탈슬러그 X",
    "mslug3": "메탈슬러그 3",
    "mslug4": "메탈슬러그 4",
    "mslug5": "메탈슬러그 5",
    "samsho": "사무라이 쇼다운",
    "samsho2": "사무라이 쇼다운 2",
    "samsho3": "사무라이 쇼다운 3",
    "samsho4": "사무라이 쇼다운 4",
    "samsho5": "사무라이 쇼다운 5",
    "fatfur": "아랑전설 (Fatal Fury)",
    "fatfur2": "아랑전설 2",
    "fatfury3": "아랑전설 3",
    "garou": "가로우: 마크 오브 더 울브스",
    "lastblad": "월화의 검사",
    "lastbld2": "월화의 검사 2",
    "orlegend": "오리엔탈 레전드 (삼국전기)",
    "kov": "삼국전기 (Knights of Valour)",
    "demonfr": "데몬 프론트 (Demon Front)",
    "sailormn": "미소녀 전사 세일러 문",
    "hook": "후크 (Hook)",
    "snowbros": "스노우 브라더스",
    "snowbro2": "스노우 브라더스 2",
    "bublbobl": "보글보글 (Bubble Bobble)",
    "cupsoc": "세이부 컵 축구 (Seibu Cup Soccer)",
    "seibucup": "세이부 컵 축구 (Seibu Cup Soccer)",
    "s1945": "스트라이커즈 1945",
    "s1945ii": "스트라이커즈 1945 II",
    "s1945iii": "스트라이커즈 1945 III",
    "gunbird": "건버드 (Gunbird)",
    "gunbird2": "건버드 2",
    "raiden": "라이덴 (Raiden)",
    "raiden2": "라이덴 2",
}

KNOWN_NEOGEO_STEMS = {
    "mslug", "mslug2", "mslugx", "mslug3", "mslug4", "mslug5",
    "kof94", "kof95", "kof96", "kof97", "kof98", "kof99", "kof2000", "kof2001", "kof2002", "kof2003",
    "samsho", "samsho2", "samsho3", "samsho4", "samsho5", "samsh5sp",
    "fatfur", "fatfur2", "fatfury3", "garou", "lastblad", "lastbld2",
    "aof", "aof2", "aof3", "rbff1", "rbff2", "rbffspec",
    "neogeo", "spinmast", "shocktro", "shocktr2", "magdrop2", "magdrop3",
    "wakuwak7", "whp", "wh2", "wh2j", "wh1", "sengoku", "sengoku2", "sengoku3",
    "viewpoin", "zedblade", "blazstar", "pulstar", "nam1975", "kotm", "kotm2",
    "twinkle", "breakers", "breakrev", "matrim", "rotd", "kizuna", "savagere",
}

KNOWN_N64_NAMES = {
    "MARIOKART64": "Mario Kart 64",
    "PUYO PUYO SUN 64": "Puyo Puyo Sun 64",
    "SUPERMARIO64": "Super Mario 64",
    "SUPER MARIO 64": "Super Mario 64",
    "STARCRAFT 64": "StarCraft 64",
    "STARFOX64": "Star Fox 64",
    "EVANGELION": "Neon Genesis Evangelion",
    "HLZELDA MASTER QUEST": "Legend of Zelda, The - Ocarina of Time - Master Quest",
    "OGREBATTLE64": "Ogre Battle 64 - Person of Lordly Caliber",
    "SHIREN 2": "Fushigi no Dungeon - Fuurai no Shiren 2 - Oni Shuurai! Shiren Jou!",
    "THE MASK OF MUJURA": "Legend of Zelda, The - Majora's Mask",
    "THE LEGEND OF ZELDA": "Legend of Zelda, The - Ocarina of Time",
    "TSUMI TO BATSU": "Sin and Punishment",
    "CONKER BFD": "Conker's Bad Fur Day",
    "PAPER MARIO KR": "Paper Mario",
    "PAPER MARIO": "Paper Mario",
    "64": "Super Robot Taisen 64",
}

# ----------------------------------------------------------------------
# 콘솔/레트로 게임 대표 한글명 내장 오프라인 매핑 사전 (1단계)
# ----------------------------------------------------------------------
KNOWN_KOREAN_TITLES = {
    # 젤다의 전설 시리즈
    "kamigami no triforce": "젤다의 전설: 신들의 트라이포스",
    "link to the past": "젤다의 전설: 신들의 트라이포스",
    "ocarina of time": "젤다의 전설: 시간의 오카리나",
    "majora": "젤다의 전설: 무쥬라의 가면",
    "minish cap": "젤다의 전설: 이상한 모자",
    "link's awakening": "젤다의 전설: 꿈꾸는 섬",
    "links awakening": "젤다의 전설: 꿈꾸는 섬",
    "oracle of ages": "젤다의 전설: 시공의 장",
    "oracle of seasons": "젤다의 전설: 대지의 장",
    "legend of zelda": "젤다의 전설",
    "zelda 2": "젤다 2: 링크의 모험",
    "zelda": "젤다의 전설",

    # 마리오 & 동키콩 시리즈
    "super mario world 2": "슈퍼 마리오 월드 2: 요시 아일랜드",
    "yoshi's island": "슈퍼 마리오: 요시 아일랜드",
    "yoshis island": "슈퍼 마리오: 요시 아일랜드",
    "yoshi island": "슈퍼 마리오: 요시 아일랜드",
    "super mario world": "슈퍼 마리오 월드",
    "super mario kart": "슈퍼 마리오 카트",
    "mario kart 64": "마리오 카트 64",
    "super mario 64": "슈퍼 마리오 64",
    "super mario all stars": "슈퍼 마리오 올스타즈",
    "super mario rpg": "슈퍼 마리오 RPG",
    "mario rpg": "슈퍼 마리오 RPG",
    "super mario bros 3": "슈퍼 마리오 브라더스 3",
    "super mario bros 2": "슈퍼 마리오 브라더스 2",
    "super mario bros": "슈퍼 마리오 브라더스",
    "mario bros": "마리오 브라더스",
    "dr mario": "닥터 마리오",
    "paper mario": "페이퍼 마리오",
    "super donkey kong 3": "동키콩 컨트리 3 (슈퍼 동키콩 3)",
    "super donkey kong 2": "동키콩 컨트리 2 (슈퍼 동키콩 2)",
    "super donkey kong": "동키콩 컨트리 (슈퍼 동키콩)",
    "donkey kong country": "동키콩 컨트리",
    "donkey kong": "동키콩",

    # 소닉 / 베어너클 / 세가 메가드라이브
    "sonic3&k": "소닉 3 & 너클즈",
    "sonic & knuckles": "소닉 & 너클즈",
    "sonic 3": "소닉 더 헤지혹 3",
    "sonic the hedgehog 3": "소닉 더 헤지혹 3",
    "sonic the hedgehog 2": "소닉 더 헤지혹 2",
    "sonic the hedgehog": "소닉 더 헤지혹",
    "bare knuckle 3": "베어 너클 3",
    "bare knuckle 2": "베어 너클 2",
    "bare knuckle": "베어 너클",
    "streets of rage 3": "베어 너클 3 (Streets of Rage 3)",
    "streets of rage 2": "베어 너클 2 (Streets of Rage 2)",
    "streets of rage": "베어 너클 (Streets of Rage)",
    "shining force ii": "샤이닝 포스 2",
    "shining force": "샤이닝 포스",
    "shining and the darkness": "샤이닝 앤 더 다크니스",
    "golden axe 3": "골든 액스 3",
    "golden axe 2": "골든 액스 2",
    "golden axe": "골든 액스 (Golden Axe)",
    "super fantasy zone": "슈퍼 판타지 존",
    "twinkle tale": "트윙클 테일",
    "valis iii": "바리스 3 (Valis III)",
    "valis sd": "SD 바리스 (Valis SD)",
    "vixen 357": "빅센 357 (Vixen 357)",
    "rent a hero": "렌트 어 히어로 (Rent A Hero)",
    "gunstar heroes": "건스타 히어로즈",
    "yuu yuu hakusho": "유유백서 마계통일편",
    "yu yu hakusho": "유유백서 마계통일편",

    # 록맨 / 메가맨 시리즈
    "rockman x3": "록맨 X3",
    "rockman x2": "록맨 X2",
    "rockman x": "록맨 X",
    "rockman 7": "록맨 7: 숙명의 대결!",
    "rockman 6": "록맨 6",
    "rockman 5": "록맨 5",
    "rockman 4": "록맨 4",
    "rockman 3": "록맨 3",
    "rockman 2": "록맨 2",
    "rockman": "록맨 (Mega Man)",
    "mega man x3": "록맨 X3 (Mega Man X3)",
    "mega man x2": "록맨 X2 (Mega Man X2)",
    "mega man x": "록맨 X (Mega Man X)",
    "mega man": "록맨 (Mega Man)",

    # 악마성 드라큘라 / 캐슬바니아 시리즈
    "akumajou dracula xx": "악마성 드라큘라 XX",
    "akumajou dracula": "악마성 드라큘라",
    "castlevania symphony of the night": "악마성 드라큘라 X: 월하의 야상곡",
    "symphony of the night": "악마성 드라큘라 X: 월하의 야상곡",
    "aria of sorrow": "악마성 드라큘라: 효월의 원무곡",
    "circle of the moon": "악마성 드라큘라: 서클 오브 더 문",
    "harmony of dissonance": "악마성 드라큘라: 백야의 협주곡",
    "rondo of blood": "악마성 드라큘라 X: 피의 윤회",
    "castlevania": "악마성 드라큘라 (Castlevania)",

    # 파이널 판타지 / 드래곤 퀘스트 / 스퀘어 & 에닉스 RPG
    "final fantasy 7": "파이널 판타지 7",
    "final fantasy 6": "파이널 판타지 6",
    "final fantasy 5": "파이널 판타지 5",
    "final fantasy 4": "파이널 판타지 4",
    "final fantasy 3": "파이널 판타지 3",
    "final fantasy 2": "파이널 판타지 2",
    "final fantasy 1": "파이널 판타지 1",
    "final fantasy": "파이널 판타지",
    "chrono trigger": "크로노 트리거",
    "chrono cross": "크로노 크로스",
    "seiken densetsu 3": "성검전설 3",
    "seiken densetsu 2": "성검전설 2",
    "seiken densetsu": "성검전설",
    "secret of mana": "성검전설 2",
    "trials of mana": "성검전설 3",
    "bahamut lagoon": "바하무트 라군",
    "live a live": "라이브 어 라이브",
    "front mission": "프론트 미션",
    "tactics ogre": "택틱스 오우거",
    "ogre battle 64": "오우거 배틀 64",
    "ogre battle": "전설의 오우거 배틀",
    "dragon quest 6": "드래곤 퀘스트 6",
    "dragon quest 5": "드래곤 퀘스트 5",
    "dragon quest 4": "드래곤 퀘스트 4",
    "dragon quest 3": "드래곤 퀘스트 3",
    "dragon quest 2": "드래곤 퀘스트 2",
    "dragon quest 1": "드래곤 퀘스트 1",
    "dragon quest": "드래곤 퀘스트",
    "tales of phantasia": "테일즈 오브 판타지아",
    "star ocean": "스타 오션",
    "valkyrie profile": "발키리 프로파일",
    "xenogears": "제노기어스",
    "alcahest": "알카헤스트 (Alcahest)",
    "assault suits valken": "중장기병 발켄 (Assault Suits Valken)",
    "harvest moon": "목장이야기 (Harvest Moon)",
    "breath of fire 2": "브레스 오브 파이어 2",
    "breath of fire": "브레스 오브 파이어",
    "ys 3": "이스 3: 원더러스 프롬 이스",
    "ys 4": "이스 4: 태양의 가면",
    "ys 5": "이스 5: 잃어버린 모래도시 케핀",
    "ys": "이스 (Ys)",

    # 바이오하자드 / 캡콤 / 액션
    "resident evil 3": "바이오하자드 3: 라스트 이스케이프",
    "resident evil 2": "바이오하자드 2",
    "resident evil": "바이오하자드 1 (Resident Evil)",
    "biohazard 3": "바이오하자드 3",
    "biohazard 2": "바이오하자드 2",
    "biohazard": "바이오하자드",
    "dino crisis 2": "디노 크라이시스 2",
    "dino crisis": "디노 크라이시스",
    "vagrant story": "베이그런트 스토리",
    "parasite eve 2": "패러사이트 이브 2",
    "parasite eve": "패러사이트 이브",
    "metal gear solid": "메탈 기어 솔리드",
    "silent hill": "사일런트 힐",
    "tekken 3": "철권 3",
    "tekken 2": "철권 2",
    "tekken": "철권",
    "final fight 3": "파이널 파이트 터프 (Final Fight 3)",
    "final fight 2": "파이널 파이트 2",
    "final fight": "파이널 파이트",
    "contra 3": "콘트라 스피리츠 (Contra III)",
    "contra": "콘트라 (Contra)",
    "super contra": "슈퍼 콘트라",
    "metalslugadvance": "메탈슬러그 어드밴스드",
    "gyakuten": "역전재판",
    "starfox64": "스타폭스 64",
    "starcraft64": "스타크래프트 64",
    "evangelion": "신세기 에반게리온",
    "shiren2": "풍래의 시렌 2",
    "tsumitobatsu": "죄와 벌: 지구의 계승자",
    "conker": "컨커의 최악의 날 (Conker's Bad Fur Day)",
}

def _resolve_korean_game_title(filename, raw_title=""):
    """깨진 EUC-KR 인코딩 자동 복원 및 내장 한글 사전 기반의 정확한 한글 타이틀 매핑"""
    name = str(raw_title or filename or "").strip()

    # 1. 파일명/타이틀 내 깨진 EUC-KR(CP949) 문자열 자동 복원
    try:
        if re.search(r"[°±²³´µ¶·¸¹º»¼½¾¿À-ÿ]", name):
            decoded = name.encode("latin1", errors="ignore").decode("euc-kr", errors="ignore")
            if re.search(r"[가-힣]", decoded):
                name = decoded
    except Exception:
        pass

    # 2. 아케이드 롬셋 고유 약칭 매핑
    stem_lower = os.path.splitext(os.path.basename(filename))[0].lower()
    if stem_lower in KNOWN_ARCADE_TITLES:
        return KNOWN_ARCADE_TITLES[stem_lower]

    # 3. 불용 태그([!], (K), (J), v1.0 등) 및 확장자 제거
    clean = re.sub(r"[\(\[\{].*?[\)\]\}]", "", name).strip()
    clean = re.sub(r"\.[a-zA-Z0-9]+$", "", clean).strip()
    clean = clean.replace("_", " ").replace("-", " ")
    clean = re.sub(r"\s+", " ", clean).strip()

    # 이미 정상 한글이 포함된 경우 정리된 한글명 반환
    if re.search(r"[가-힣]", clean):
        return clean

    # 4. 내장 한글 매핑 사전(KNOWN_KOREAN_TITLES) 검색 (2-Pass 고정밀 매칭)
    norm_key = re.sub(r"[^a-zA-Z0-9\s]", " ", clean).lower()
    norm_key = re.sub(r"\s+", " ", norm_key).strip()

    # Pass 1: 완전 일치 (100% 우선순위)
    if norm_key in KNOWN_KOREAN_TITLES:
        return KNOWN_KOREAN_TITLES[norm_key]

    # Pass 2: 단어 경계(Word Boundary) 기준 부분 일치 (키 길이 긴 순서로 우선 매칭)
    sorted_dict = sorted(KNOWN_KOREAN_TITLES.items(), key=lambda x: len(x[0]), reverse=True)
    for eng_key, kor_title in sorted_dict:
        if len(eng_key) >= 4 and re.search(r"\b" + re.escape(eng_key) + r"\b", norm_key):
            return kor_title

    return clean or name


def _is_bios_file(filename):
    """주요 에뮬레이터 및 아케이드(MAME/FBNeo) 기판 바이오스 파일 여부 확인"""
    fname = os.path.basename(filename).lower()
    stem = os.path.splitext(fname)[0]
    if stem in KNOWN_BIOS_STEMS or "bios" in stem:
        return True
    if fname.startswith("bios_") or fname.startswith("scph") or fname in ("disksys.rom", "syscard3.pce"):
        return True
    return False


def _detect_rom_info(file_path):
    """ROM 파일의 코어(core), 플랫폼(platform), 타이틀(title), 게임코드 등을 자동 감지합니다."""
    info = {
        "core": "gba",
        "platform": "GBA",
        "title": "",
        "game_code": "",
        "maker_code": "",
    }
    ext = os.path.splitext(file_path)[1].lower()
    raw_data = None

    if zipfile.is_zipfile(file_path):
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                matching = []
                for n in z.namelist():
                    e = os.path.splitext(n)[1].lower()
                    if e in SUPPORTED_SYSTEMS:
                        matching.append((n, e))

                if matching:
                    inner_name, inner_ext = matching[0]
                    # GP32 스마트미디어 카드 (.smc) 및 미지원 기종 오인 방지
                    fpath_lower = file_path.lower()
                    if inner_ext == ".smc" and ("gp32" in fpath_lower or "gamepark" in fpath_lower or "/gp32/" in fpath_lower):
                        info["core"] = "_skip_"
                        info["platform"] = "_skip_"
                    else:
                        sys_info = SUPPORTED_SYSTEMS[inner_ext]
                        info["core"] = sys_info["core"]
                        info["platform"] = sys_info["platform"]
                        with z.open(inner_name) as zf:
                            raw_data = zf.read(0x10000)
                        base_inner = os.path.splitext(os.path.basename(inner_name))[0]
                        clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                        if clean_inner:
                            info["title"] = clean_inner
                else:
                    # 확장자가 없는 칩셋 파일(.bin, .u1, .ic1 등) 바이너리 분석
                    fname_base = os.path.basename(file_path)
                    clean_fname = re.sub(r"^\.+(temp_upload_)?", "", fname_base)
                    stem = os.path.splitext(clean_fname)[0].lower()

                    # 1. 바이오스 / MAME 기판 칩셋 디바이스는 게임 목록 등록에서 제외 (bios/ 폴더로 분류 대상)
                    if _is_bios_file(clean_fname) or stem in KNOWN_BIOS_STEMS:
                        info["core"] = "_skip_"
                        info["platform"] = "_skip_"
                    # 2. 내장 아케이드 사전(KNOWN_ARCADE_TITLES)에 등록된 인기 타이틀
                    elif stem in KNOWN_ARCADE_TITLES:
                        info["core"] = "arcade"
                        info["platform"] = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"
                        info["title"] = KNOWN_ARCADE_TITLES[stem]
                    else:
                        # 3. ZIP 내부 모든 파일 바이너리 헤더 전수 조사 (메가드라이브, SNES, NES, GBA, GB, N64 등)
                        detected_by_header = False
                        for inner_f in z.namelist():
                            if inner_f.startswith(".") or inner_f.endswith("/"):
                                continue
                            try:
                                with z.open(inner_f) as zf:
                                    sample = zf.read(0x10000)
                                if not sample:
                                    continue

                                # 3-1. Sega Mega Drive / Genesis 헤더 (0x100:0x120)
                                if len(sample) >= 0x150 and (b"SEGA" in sample[0x100:0x110] or b"GENESIS" in sample[0x100:0x110] or b"MEGA DRIVE" in sample[0x100:0x120]):
                                    info["core"] = "segaMD"
                                    info["platform"] = "Genesis"
                                    raw_data = sample
                                    base_inner = os.path.splitext(os.path.basename(inner_f))[0]
                                    clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                    if clean_inner:
                                        info["title"] = clean_inner
                                    detected_by_header = True
                                    break

                                # 3-2. Nintendo NES / Famicom iNES 헤더 (0x00)
                                elif len(sample) >= 16 and sample[:4] == b"NES\x1a":
                                    info["core"] = "nes"
                                    info["platform"] = "NES"
                                    raw_data = sample
                                    base_inner = os.path.splitext(os.path.basename(inner_f))[0]
                                    clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                    if clean_inner:
                                        info["title"] = clean_inner
                                    detected_by_header = True
                                    break

                                # 3-3. Nintendo Game Boy Advance 헤더 (0x04)
                                elif len(sample) >= 0xC0 and sample[0x04:0x08] == b" \x00\x00\xea":
                                    info["core"] = "gba"
                                    info["platform"] = "GBA"
                                    raw_data = sample
                                    base_inner = os.path.splitext(os.path.basename(inner_f))[0]
                                    clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                    if clean_inner:
                                        info["title"] = clean_inner
                                    detected_by_header = True
                                    break

                                # 3-4. Nintendo N64 매직 넘버 (0x00)
                                elif len(sample) >= 0x40 and sample[:4] in (b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"):
                                    info["core"] = "n64"
                                    info["platform"] = "N64"
                                    raw_data = sample
                                    base_inner = os.path.splitext(os.path.basename(inner_f))[0]
                                    clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                    if clean_inner:
                                        info["title"] = clean_inner
                                    detected_by_header = True
                                    break

                                # 3-5. Nintendo SNES / Super Famicom 내부 롬 헤더 (0x7FC0 / 0xFFC0)
                                elif len(sample) >= 0x8000 and any(len(sample) >= off + 21 and not "".join(chr(b) for b in sample[off:off+21] if 32 <= b <= 126).strip().startswith("???") for off in (0x7FC0, 0xFFC0)):
                                    info["core"] = "snes"
                                    info["platform"] = "SNES"
                                    raw_data = sample
                                    base_inner = os.path.splitext(os.path.basename(inner_f))[0]
                                    clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                    if clean_inner:
                                        info["title"] = clean_inner
                                    detected_by_header = True
                                    break
                            except Exception:
                                pass

                        if not detected_by_header:
                            # 4. 헤더가 없는 경우 상위 디렉터리명 기반 콘솔 롬셋 판별
                            fpath_lower = file_path.lower().replace("\\", "/")
                            parts = fpath_lower.split("/")
                            parent_dir = parts[-2] if len(parts) >= 2 else ""

                            detected_console = False
                            if parent_dir in ("snes", "sfc", "super_nintendo"):
                                info["core"] = "snes"
                                info["platform"] = "SNES"
                                detected_console = True
                            elif parent_dir in ("megadriv", "genesis", "sega", "md", "segamd"):
                                info["core"] = "segaMD"
                                info["platform"] = "Genesis"
                                detected_console = True
                            elif parent_dir in ("nes", "fc", "famicom"):
                                info["core"] = "nes"
                                info["platform"] = "NES"
                                detected_console = True
                            elif parent_dir in ("gba", "gameboy_advance"):
                                info["core"] = "gba"
                                info["platform"] = "GBA"
                                detected_console = True
                            elif parent_dir in ("gb", "gbc", "gameboy"):
                                info["core"] = "gb"
                                info["platform"] = "GB"
                                detected_console = True
                            elif parent_dir in ("n64", "nintendo64"):
                                info["core"] = "n64"
                                info["platform"] = "N64"
                                detected_console = True
                            elif parent_dir in ("psx", "ps1", "playstation", "isos"):
                                info["core"] = "psx"
                                info["platform"] = "PS1"
                                detected_console = True
                            elif parent_dir in ("nds", "nintendods"):
                                info["core"] = "nds"
                                info["platform"] = "NDS"
                                detected_console = True

                            if not detected_console:
                                # 5. 상위 폴더가 콘솔이 아니면 아케이드/네오지오로 판별
                                info["core"] = "arcade"
                                info["platform"] = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"

                            raw_title = clean_fname.split(".")[0].replace("_", " ").replace("-", " ")
                            info["title"] = raw_title.title() if not raw_title.isupper() else raw_title
        except Exception as e:
            logger.debug(f"[{SELF_ID}] Zip inspect error: {e}")
    elif ext == ".7z":
        try:
            import py7zr
            if py7zr.is_7zfile(file_path):
                with tempfile.TemporaryDirectory() as tmpdir:
                    with py7zr.SevenZipFile(file_path, mode="r") as z7:
                        z7.extract(path=tmpdir)
                    extracted_files = [f for f in os.listdir(tmpdir) if not f.startswith(".")]

                    matching = []
                    for fname in extracted_files:
                        e = os.path.splitext(fname)[1].lower()
                        if e in SUPPORTED_SYSTEMS:
                            matching.append((fname, e))

                    if matching:
                        inner_name, inner_ext = matching[0]
                        sys_info = SUPPORTED_SYSTEMS[inner_ext]
                        info["core"] = sys_info["core"]
                        info["platform"] = sys_info["platform"]
                        with open(os.path.join(tmpdir, inner_name), "rb") as ef:
                            raw_data = ef.read(0x10000)
                        base_inner = os.path.splitext(os.path.basename(inner_name))[0]
                        clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                        if clean_inner:
                            info["title"] = clean_inner
                    elif extracted_files:
                        # .bin / .rom 확장자이거나 폴더명/파일명 기반 코어 분석
                        for fname in extracted_files:
                            full_ef = os.path.join(tmpdir, fname)
                            with open(full_ef, "rb") as ef:
                                header_data = ef.read(0x10000)

                            # 세가 메가드라이브 (SEGA / GENESIS / MEGA DRIVE 헤더)
                            if len(header_data) >= 0x120 and (b"SEGA" in header_data[0x100:0x110] or b"GENESIS" in header_data[0x100:0x110] or b"MEGA DRIVE" in header_data[0x100:0x120]):
                                info["core"] = "segaMD"
                                info["platform"] = "Genesis"
                                raw_data = header_data
                                base_inner = os.path.splitext(os.path.basename(fname))[0]
                                info["title"] = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                break
                            # GBA (0x04:0x08 == b' \x00\x00\xea')
                            elif len(header_data) >= 0xC0 and header_data[0x04:0x08] == b" \x00\x00\xea":
                                info["core"] = "gba"
                                info["platform"] = "GBA"
                                raw_data = header_data
                                base_inner = os.path.splitext(os.path.basename(fname))[0]
                                info["title"] = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                break
                            # N64
                            elif len(header_data) >= 0x40 and header_data[:4] in (b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"):
                                info["core"] = "n64"
                                info["platform"] = "N64"
                                raw_data = header_data
                                base_inner = os.path.splitext(os.path.basename(fname))[0]
                                info["title"] = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                break
                            # SNES
                            elif len(header_data) >= 0x8000 and any(len(header_data) >= off + 21 and not "".join(chr(b) for b in header_data[off:off+21] if 32 <= b <= 126).strip().startswith("???") for off in (0x7FC0, 0xFFC0)):
                                info["core"] = "snes"
                                info["platform"] = "SNES"
                                raw_data = header_data
                                base_inner = os.path.splitext(os.path.basename(fname))[0]
                                info["title"] = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                                break

                        if info["core"] == "gba" and ("megadriv" in file_path.lower() or "genesis" in file_path.lower() or "md" in file_path.lower()):
                            info["core"] = "segaMD"
                            info["platform"] = "Genesis"

                    if not info["title"]:
                        stem = os.path.splitext(os.path.basename(file_path))[0].lower()
                        if stem in KNOWN_ARCADE_TITLES:
                            info["core"] = "arcade"
                            info["platform"] = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"
                            info["title"] = KNOWN_ARCADE_TITLES[stem]
        except Exception as e:
            logger.debug(f"[{SELF_ID}] 7z inspect error: {e}")
    else:
        if ext in SUPPORTED_SYSTEMS:
            sys_info = SUPPORTED_SYSTEMS[ext]
            info["core"] = sys_info["core"]
            info["platform"] = sys_info["platform"]
        try:
            with open(file_path, "rb") as f:
                raw_data = f.read(0x10000)
        except Exception:
            pass

    # 플랫폼별 세부 헤더 타이틀 추출
    if info["core"] == "snes":
        if raw_data and len(raw_data) >= 0x8000:
            for offset in (0x7FC0, 0xFFC0, 0x81C0, 0x101C0):
                if len(raw_data) >= offset + 21:
                    candidate = raw_data[offset:offset+21]
                    clean = "".join(chr(b) for b in candidate if 32 <= b <= 126).strip()
                    if len(clean) >= 4 and not clean.startswith("???"):
                        info["title"] = clean
                        break
    elif info["core"] == "gba":
        if raw_data and len(raw_data) >= 0xC0:
            raw_title = raw_data[0xA0:0xAC]
            title = "".join(chr(b) for b in raw_title if 32 <= b <= 126).strip()
            raw_code = raw_data[0xAC:0xB0]
            game_code = "".join(chr(b) for b in raw_code if 32 <= b <= 126).strip()
            raw_maker = raw_data[0xB0:0xB2]
            maker_code = "".join(chr(b) for b in raw_maker if 32 <= b <= 126).strip()
            if title: info["title"] = title
            if game_code: info["game_code"] = game_code
            if maker_code: info["maker_code"] = maker_code
    elif info["core"] == "n64":
        if raw_data and len(raw_data) >= 0x40:
            magic = raw_data[:4]
            raw_title = raw_data[0x20:0x34]
            if magic == b"\x37\x80\x40\x12":  # .v64 byte swapped
                b = bytearray(raw_title)
                for i in range(0, len(b), 2):
                    if i + 1 < len(b):
                        b[i], b[i+1] = b[i+1], b[i]
                n64_title = "".join(chr(c) for c in b if 32 <= c <= 126).strip()
            elif magic == b"\x40\x12\x37\x80":  # .n64 little endian
                b = bytearray(raw_title)
                for i in range(0, len(b), 4):
                    if i + 3 < len(b):
                        b[i], b[i+1], b[i+2], b[i+3] = b[i+3], b[i+2], b[i+1], b[i]
                n64_title = "".join(chr(c) for c in b if 32 <= c <= 126).strip()
            else:  # .z64 big endian
                n64_title = "".join(chr(c) for c in raw_title if 32 <= c <= 126).strip()
            if n64_title:
                info["title"] = n64_title
    elif info["core"] == "segaMD":
        if raw_data and len(raw_data) >= 0x150:
            # 0x120:0x150 위치의 일본/해외 공식 게임명
            raw_title = raw_data[0x120:0x150]
            md_title = "".join(chr(b) for b in raw_title if 32 <= b <= 126).strip()
            if md_title and len(md_title) >= 3:
                info["title"] = md_title

    return info


class BookoasisGamebooksMetadataProvider(BaseMetadataProvider):
    id = "bookoasis_gamebooks"
    name = "Game Books"
    is_searchable = False

    category_tab = {
        "title": "Game Books",
        "icon": "fa-solid fa-gamepad",
        "order": 88,
        "sessions": ["general"],
    }

    config_schema = []

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/madnite1/bookoasis_gamebooks/main",
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
        "files": [
            "bookoasis_gamebooks.py",
            "__init__.py",
            "VERSION",
            "LICENSE",
            "index.html",
            "style.css",
            "script.js",
            "README.md",
            "requirements.txt",
        ],
    }

    # ------------------------------------------------------------------
    # 영속 데이터 디렉터리 헬퍼 (BookOasis 표준 plugins/data/bookoasis_gamebooks)
    # ------------------------------------------------------------------
    def _get_data_dir(self):
        """플러그인 영속 데이터 디렉터리 (../../data/bookoasis_gamebooks/)"""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.normpath(os.path.join(plugin_dir, "..", "..", "data", self.id))
        try:
            os.makedirs(data_dir, exist_ok=True)
        except Exception:
            pass
        return data_dir

    def _get_roms_dir(self):
        """기본 롬 파일 디렉터리 (../../data/bookoasis_gamebooks/roms/) - 전체 유저 공유"""
        roms_dir = os.path.join(self._get_data_dir(), "roms")
        os.makedirs(roms_dir, exist_ok=True)
        return roms_dir

    def _get_user_saves_dir(self, user_id=None):
        """유저별 세이브 파일 디렉터리 (../../data/bookoasis_gamebooks/saves/user_{user_id}/)"""
        if user_id is None:
            user_id = _get_current_user_id()
        saves_dir = os.path.join(self._get_data_dir(), "saves", f"user_{user_id}")
        os.makedirs(saves_dir, exist_ok=True)
        return saves_dir

    def _get_covers_dir(self):
        """커버 아트 이미지 디렉터리 (설정된 COVERS_PATH 또는 기본 ../../data/bookoasis_gamebooks/covers/)"""
        custom_path = self._get_setting("COVERS_PATH", "").strip()
        if custom_path:
            try:
                os.makedirs(custom_path, exist_ok=True)
                if os.path.exists(custom_path) and os.path.isdir(custom_path):
                    return custom_path
            except Exception as e:
                logger.warning(f"[{SELF_ID}] Custom covers dir error ({custom_path}): {e}")
        default_dir = os.path.join(self._get_data_dir(), "covers")
        os.makedirs(default_dir, exist_ok=True)
        return default_dir

    def _migrate_covers_to_custom_dir(self, new_dir):
        """기존 커버 아트 폴더의 파일들을 새로 설정된 폴더로 이동 및 DB cover_path 경로 갱신"""
        if not new_dir:
            return 0
        new_dir = os.path.abspath(new_dir.strip())
        os.makedirs(new_dir, exist_ok=True)

        old_default_dir = os.path.abspath(os.path.join(self._get_data_dir(), "covers"))
        moved_count = 0

        # 1. 기본 covers/ 폴더 내 파일 이동
        if os.path.exists(old_default_dir) and old_default_dir != new_dir:
            try:
                for f in os.listdir(old_default_dir):
                    if f.startswith("."):
                        continue
                    src_f = os.path.join(old_default_dir, f)
                    if os.path.isfile(src_f):
                        dst_f = os.path.join(new_dir, f)
                        try:
                            if not os.path.exists(dst_f):
                                shutil.move(src_f, dst_f)
                            else:
                                os.remove(src_f)
                            moved_count += 1
                        except Exception as e:
                            logger.error(f"[{SELF_ID}] Move cover file error ({f}): {e}")
            except Exception as e:
                logger.error(f"[{SELF_ID}] Covers migration error: {e}")

        # 2. DB에 기록된 cover_path 일괄 갱신
        try:
            rows = self._db_query("SELECT id, cover_path FROM games WHERE cover_path IS NOT NULL AND cover_path != ''")
            for r in rows:
                c_path = r["cover_path"]
                if c_path and os.path.exists(c_path):
                    fname = os.path.basename(c_path)
                    new_c_path = os.path.join(new_dir, fname)
                    if c_path != new_c_path:
                        if not os.path.exists(new_c_path):
                            try:
                                shutil.move(c_path, new_c_path)
                            except Exception:
                                pass
                        self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (new_c_path, r["id"]))
                else:
                    # 파일은 new_dir에 이미 이동되었을 수 있으므로 확인 후 DB 갱신
                    game_id = r["id"]
                    for ext in (".png", ".jpg", ".jpeg", ".webp"):
                        check_p = os.path.join(new_dir, f"{game_id}{ext}")
                        if os.path.exists(check_p):
                            self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (check_p, game_id))
                            break
        except Exception as e:
            logger.error(f"[{SELF_ID}] DB cover path update error: {e}")

        logger.info(f"[{SELF_ID}] Migrated {moved_count} covers to {new_dir}")
        return moved_count

    def _get_bios_dir(self):
        """시스템 바이오스 파일 디렉터리 (설정된 BIOS_PATH 또는 기본 ../../data/bookoasis_gamebooks/bios/)"""
        custom_path = self._get_setting("BIOS_PATH", "").strip()
        if custom_path:
            try:
                os.makedirs(custom_path, exist_ok=True)
                if os.path.exists(custom_path) and os.path.isdir(custom_path):
                    return custom_path
            except Exception as e:
                logger.warning(f"[{SELF_ID}] Custom bios dir error ({custom_path}): {e}")
        bios_dir = os.path.join(self._get_data_dir(), "bios")
        os.makedirs(bios_dir, exist_ok=True)
        return bios_dir

    def _migrate_bios_to_custom_dir(self, new_dir):
        """기존 바이오스 폴더의 파일들을 새로 설정된 폴더로 이동"""
        if not new_dir:
            return 0
        new_dir = os.path.abspath(new_dir.strip())
        os.makedirs(new_dir, exist_ok=True)

        old_default_dir = os.path.abspath(os.path.join(self._get_data_dir(), "bios"))
        moved_count = 0

        if os.path.exists(old_default_dir) and old_default_dir != new_dir:
            try:
                for f in os.listdir(old_default_dir):
                    if f.startswith("."):
                        continue
                    src_f = os.path.join(old_default_dir, f)
                    if os.path.isfile(src_f):
                        dst_f = os.path.join(new_dir, f)
                        try:
                            if not os.path.exists(dst_f):
                                shutil.move(src_f, dst_f)
                            else:
                                os.remove(src_f)
                            moved_count += 1
                        except Exception as e:
                            logger.error(f"[{SELF_ID}] Move bios file error ({f}): {e}")
            except Exception as e:
                logger.error(f"[{SELF_ID}] Bios migration error: {e}")

        logger.info(f"[{SELF_ID}] Migrated {moved_count} bios files to {new_dir}")
        return moved_count

    def _get_db_path(self):
        """설정 및 게임 메타 SQLite DB 경로"""
        return os.path.join(self._get_data_dir(), "gba.db")

    def __init__(self):
        super().__init__()
        self._migrate_old_plugin_data()
        self._migrate_bios_files()
        self._init_db()

    def _migrate_bios_files(self):
        """roms/ 폴더에 혼재된 바이오스 및 MAME 기판/디바이스 파일을 bios/ 폴더로 자동 분류 이동"""
        roms_dir = self._get_roms_dir()
        bios_dir = self._get_bios_dir()
        if not os.path.exists(roms_dir):
            return

        moved_count = 0
        try:
            for root, _, files in os.walk(roms_dir):
                for f in files:
                    if f.startswith("."):
                        continue
                    full_p = os.path.join(root, f)
                    if not os.path.isfile(full_p):
                        continue

                    is_bios = _is_bios_file(f)
                    if not is_bios and f.lower().endswith(".zip"):
                        rom_info = _detect_rom_info(full_p)
                        if rom_info.get("platform") == "_skip_":
                            is_bios = True

                    if is_bios:
                        target_p = os.path.join(bios_dir, f)
                        try:
                            if not os.path.exists(target_p):
                                shutil.move(full_p, target_p)
                            else:
                                os.remove(full_p)
                            moved_count += 1
                        except Exception as ex:
                            logger.debug(f"[{SELF_ID}] Bios migration move error ({f}): {ex}")
            if moved_count > 0:
                logger.info(f"[{SELF_ID}] Successfully migrated {moved_count} bios/device files from roms/ to bios/")
        except Exception as e:
            logger.error(f"[{SELF_ID}] Bios migration failed: {e}")

    def _migrate_old_plugin_data(self):
        """이전 bookoasis_gba 또는 bookoasis_emulatorjs 데이터 디렉터리가 있을 경우 bookoasis_gamebooks로 마이그레이션"""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        new_data_dir = self._get_data_dir()

        for legacy_id in ("bookoasis_gba", "bookoasis_emulatorjs"):
            old_data_dir = os.path.normpath(os.path.join(plugin_dir, "..", "..", "data", legacy_id))
            if os.path.exists(old_data_dir) and old_data_dir != new_data_dir:
                try:
                    for item in os.listdir(old_data_dir):
                        src_p = os.path.join(old_data_dir, item)
                        dst_p = os.path.join(new_data_dir, item)
                        if not os.path.exists(dst_p):
                            if os.path.isdir(src_p):
                                shutil.copytree(src_p, dst_p)
                            else:
                                shutil.copy2(src_p, dst_p)
                    logger.info(f"[{SELF_ID}] Successfully migrated data from {legacy_id} to {self.id}")
                except Exception as e:
                    logger.warning(f"[{SELF_ID}] Data migration warning from {legacy_id}: {e}")

    def _init_db(self):
        """SQLite 테이블 스키마 초기화 (공유 롬 메타 + 유저별 플레이 데이터 분리)"""
        db_path = self._get_db_path()
        with _DB_LOCK:
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS games (
                        id TEXT PRIMARY KEY,
                        filename TEXT,
                        file_path TEXT,
                        title TEXT,
                        game_code TEXT,
                        maker_code TEXT,
                        core TEXT DEFAULT 'gba',
                        platform TEXT DEFAULT 'GBA',
                        size_bytes INTEGER DEFAULT 0,
                        mtime REAL DEFAULT 0,
                        added_at TEXT,
                        cover_path TEXT
                    )"""
                )
                for col, ctype in (("core", "TEXT DEFAULT 'gba'"), ("platform", "TEXT DEFAULT 'GBA'"), ("mtime", "REAL DEFAULT 0")):
                    try:
                        conn.execute(f"ALTER TABLE games ADD COLUMN {col} {ctype}")
                    except Exception:
                        pass

                conn.execute(
                    """CREATE TABLE IF NOT EXISTS user_game_data (
                        user_id INTEGER,
                        game_id TEXT,
                        is_favorite INTEGER DEFAULT 0,
                        last_played_at TEXT,
                        play_count INTEGER DEFAULT 0,
                        PRIMARY KEY (user_id, game_id)
                    )"""
                )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )"""
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"[{SELF_ID}] DB Init error: {e}")

    # ------------------------------------------------------------------
    # DB 헬퍼 함수
    # ------------------------------------------------------------------
    def _db_query(self, query, args=()):
        db_path = self._get_db_path()
        with _DB_LOCK:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute(query, args)
                rows = [dict(r) for r in cur.fetchall()]
                return rows
            finally:
                conn.close()

    def _db_execute(self, query, args=()):
        db_path = self._get_db_path()
        with _DB_LOCK:
            conn = sqlite3.connect(db_path, timeout=10)
            try:
                cur = conn.cursor()
                cur.execute(query, args)
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def _auto_fetch_and_save_cover(self, game_id, platform_or_core, filename, file_path=None, raw_title=""):
        """Libretro CDN(1차) -> ScreenScraper(2차, Key필요) -> IGDB(3차, Key필요) 순으로 커버 아트를 자동 다운로드/등록합니다."""
        try:
            # 이미 커버가 등록되어 있거나 로컬 파일이 존재하는지 검사
            rows = self._db_query("SELECT cover_path FROM games WHERE id = ?", (game_id,))
            if rows and rows[0]["cover_path"] and os.path.exists(rows[0]["cover_path"]):
                return rows[0]["cover_path"]

            covers_dir = self._get_covers_dir()
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                existing_cover = os.path.join(covers_dir, f"{game_id}{ext}")
                if os.path.exists(existing_cover):
                    self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (existing_cover, game_id))
                    return existing_cover

            # 1차: Libretro Thumbnails CDN (무료/API Key 불필요)
            art_bytes = _fetch_libretro_artwork(platform_or_core, filename, raw_title=raw_title)
            if art_bytes:
                save_cover_path = os.path.join(covers_dir, f"{game_id}.png")
                with open(save_cover_path, "wb") as f:
                    f.write(art_bytes)
                self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (save_cover_path, game_id))
                logger.info(f"[{SELF_ID}] Auto-fetched Libretro cover for {filename} -> {save_cover_path}")
                return save_cover_path

            # 2차: ScreenScraper API (설정에 Key가 등록된 경우에만 실행)
            ss_devid = self._get_setting("SS_DEVID", "").strip()
            ss_devpwd = self._get_setting("SS_DEVPASSWORD", "").strip()
            if ss_devid and ss_devpwd:
                sc_conf = {
                    "ss_devid": ss_devid,
                    "ss_devpassword": ss_devpwd,
                    "ss_user": self._get_setting("SS_USER", "").strip(),
                    "ss_password": self._get_setting("SS_PASSWORD", "").strip(),
                }
                art_bytes = _fetch_screenscraper_artwork(file_path, platform_or_core, filename, sc_conf)
                if art_bytes:
                    save_cover_path = os.path.join(covers_dir, f"{game_id}.png")
                    with open(save_cover_path, "wb") as f:
                        f.write(art_bytes)
                    self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (save_cover_path, game_id))
                    logger.info(f"[{SELF_ID}] Auto-fetched ScreenScraper cover for {filename} -> {save_cover_path}")
                    return save_cover_path

            # 3차: IGDB API (설정에 Key가 등록된 경우에만 실행)
            igdb_id = self._get_setting("IGDB_CLIENT_ID", "").strip()
            igdb_sec = self._get_setting("IGDB_CLIENT_SECRET", "").strip()
            if igdb_id and igdb_sec:
                igdb_conf = {
                    "igdb_client_id": igdb_id,
                    "igdb_client_secret": igdb_sec,
                }
                search_title = raw_title or os.path.splitext(filename)[0]
                art_bytes = _fetch_igdb_artwork(search_title, igdb_conf)
                if art_bytes:
                    save_cover_path = os.path.join(covers_dir, f"{game_id}.jpg")
                    with open(save_cover_path, "wb") as f:
                        f.write(art_bytes)
                    self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (save_cover_path, game_id))
                    logger.info(f"[{SELF_ID}] Auto-fetched IGDB cover for {filename} -> {save_cover_path}")
                    return save_cover_path

        except Exception as e:
            logger.debug(f"[{SELF_ID}] Auto cover cascade match error ({filename}): {e}")
        return None

    # ------------------------------------------------------------------
    def _check_and_update_game(self, game_id):
        """특정 게임의 ROM 파일 존재 여부 검사 및 누락된 커버 검색/세팅"""
        rows = self._db_query("SELECT * FROM games WHERE id = ?", (game_id,))
        if not rows:
            return {"exists": False, "deleted": False, "cover_updated": False}

        game = rows[0]
        file_path = game.get("file_path") or ""

        # 1. 파일 존재 여부 검사
        if not file_path or not os.path.exists(file_path):
            self._db_execute("DELETE FROM games WHERE id = ?", (game_id,))
            self._db_execute("DELETE FROM user_game_data WHERE game_id = ?", (game_id,))
            return {"exists": False, "deleted": True, "cover_updated": False}

        # 2. 기종/코어 재검증 (과거 잘못 등록된 arcade/other 기종 보정)
        rom_info = _detect_rom_info(file_path)
        if rom_info.get("platform") != "_skip_":
            detected_core = rom_info.get("core") or rom_info.get("platform")
            detected_platform = rom_info.get("platform") or detected_core
            if detected_core and (detected_core != game.get("core") or detected_platform != game.get("platform")):
                self._db_execute("UPDATE games SET core = ?, platform = ? WHERE id = ?", (detected_core, detected_platform, game_id))
                game["core"] = detected_core
                game["platform"] = detected_platform

        core = game.get("core") or game.get("platform") or ""
        filename = game.get("filename") or ""
        raw_title = rom_info.get("title") or game.get("title") or ""
        clean_title = _resolve_korean_game_title(filename, raw_title)
        if clean_title and clean_title != game.get("title"):
            self._db_execute("UPDATE games SET title = ? WHERE id = ?", (clean_title, game_id))

        cover_path = game.get("cover_path") or ""
        cover_ok = bool(cover_path and os.path.exists(cover_path))
        cover_updated = False

        if not cover_ok:
            new_cover = self._auto_fetch_and_save_cover(game_id, core, filename, file_path=file_path, raw_title=clean_title or raw_title)
            if new_cover:
                cover_updated = True
                cover_path = new_cover

        return {
            "exists": True,
            "deleted": False,
            "cover_updated": cover_updated,
            "cover_url": f"{ROUTE_BASE}/cover/{game_id}" if (cover_path and os.path.exists(cover_path)) else None,
        }

    def _scan_roms(self, new_only=False):
        """기본 roms 폴더 및 설정된 추가 경로의 ROM을 스캔하여 DB에 동기화합니다.
        new_only가 True이면 기존에 등록된 게임의 커버 검색은 건너뛰고 새로 발견된 게임만 등록 및 커버를 검색합니다.
        """
        self._migrate_bios_files()

        scan_dirs = [self._get_roms_dir()]

        extra_path = self._get_setting("EXTRA_ROMS_PATH", "").strip()
        if extra_path and os.path.isdir(extra_path):
            scan_dirs.append(extra_path)

        bios_dir = os.path.abspath(self._get_bios_dir())
        covers_dir = os.path.abspath(self._get_covers_dir())

        allowed_exts = set(SUPPORTED_SYSTEMS.keys()) | {".zip", ".7z"}
        found_files = {}

        for sdir in scan_dirs:
            try:
                for root, _, files in os.walk(sdir):
                    abs_root = os.path.abspath(root)
                    # 바이오스 폴더 또는 커버 폴더 하위 경로는 롬 스캔 대상에서 원천 제외
                    if abs_root.startswith(bios_dir) or abs_root.startswith(covers_dir):
                        continue

                    for f in files:
                        if _is_bios_file(f):
                            continue
                        ext = os.path.splitext(f)[1].lower()
                        if ext in allowed_exts:
                            full_p = os.path.join(root, f)
                            try:
                                stat = os.stat(full_p)
                                sz = stat.st_size
                                mt = stat.st_mtime
                                rel = os.path.relpath(full_p, sdir)
                                gid = _sanitize_id(f"{os.path.basename(sdir)}_{rel}")
                                found_files[gid] = {
                                    "id": gid,
                                    "filename": f,
                                    "file_path": full_p,
                                    "size_bytes": sz,
                                    "mtime": mt,
                                }
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"[{SELF_ID}] Scan dir error ({sdir}): {e}")

        existing_games = {g["id"]: g for g in self._db_query("SELECT * FROM games")}
        now_str = _get_kst_now_str()

        # 병렬 분석이 필요한 파일 목록 선별
        files_to_process = []
        covers_to_fetch = []

        for gid, info in found_files.items():
            existing = existing_games.get(gid)
            if existing:
                # 파일 크기나 수정 시간이 동일한 경우 불필요한 바이너리 재분석 0ms 스킵
                if abs((existing.get("mtime") or 0) - info["mtime"]) < 1.0 and existing.get("size_bytes") == info["size_bytes"]:
                    # 커버만 누락된 경우 백그라운드 다운로드 큐에 추가
                    c_path = existing.get("cover_path")
                    if not new_only and (not c_path or not os.path.exists(c_path)):
                        covers_to_fetch.append((
                            gid,
                            existing.get("core") or existing.get("platform") or "gba",
                            info["filename"],
                            info["file_path"],
                            existing.get("title") or info["filename"]
                        ))
                    continue

            files_to_process.append(info)

        def _process_single_rom(info):
            try:
                gid = info["id"]
                rom_info = _detect_rom_info(info["file_path"])
                if rom_info.get("platform") == "_skip_":
                    return None

                raw_name = _strip_romm_name_prefix(os.path.splitext(info["filename"])[0])
                header_title = rom_info.get("title") or ""
                mapped_header = KNOWN_N64_NAMES.get(header_title.upper().replace("_", " ").replace("-", " ").strip()) or KNOWN_N64_NAMES.get(header_title.upper()) or header_title
                clean_title = _resolve_korean_game_title(info["filename"], raw_name)

                return {
                    "gid": gid,
                    "info": info,
                    "rom_info": rom_info,
                    "clean_title": clean_title,
                    "mapped_header": mapped_header,
                }
            except Exception as ex:
                logger.debug(f"[{SELF_ID}] Process single rom error ({info.get('filename')}): {ex}")
                return None

        # ThreadPoolExecutor를 이용한 멀티스레드 병렬 바이너리 분석
        if files_to_process:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(_process_single_rom, files_to_process))

            for res in results:
                if not res:
                    continue
                gid = res["gid"]
                info = res["info"]
                rom_info = res["rom_info"]
                clean_title = res["clean_title"]
                mapped_header = res["mapped_header"]

                # 기종에 맞지 않는 폴더에 위치한 롬 파일 자동 정리 (올바른 코어 하위 폴더로 이동)
                curr_file_path = info["file_path"]
                curr_dir = os.path.dirname(os.path.abspath(curr_file_path))
                base_dir = os.path.dirname(curr_dir)  # 예: /mnt/gdrive/emulatorjs/roms

                target_core_folder = (rom_info.get("core") or rom_info.get("platform") or "other").lower()
                target_core_folder = re.sub(r"[^a-zA-Z0-9_\-]", "_", target_core_folder).strip() or "other"

                # 현재 위치한 폴더명이 해당 코어 이름과 다르면 올바른 코어 폴더로 이동
                current_folder_name = os.path.basename(curr_dir).lower()
                if current_folder_name != target_core_folder and current_folder_name not in ("roms", ""):
                    try:
                        ideal_dir = os.path.join(base_dir, target_core_folder)
                        os.makedirs(ideal_dir, exist_ok=True)
                        dest_file_path = os.path.join(ideal_dir, info["filename"])
                        if not os.path.exists(dest_file_path):
                            shutil.move(curr_file_path, dest_file_path)
                            curr_file_path = dest_file_path
                            info["file_path"] = dest_file_path
                            logger.info(f"[{SELF_ID}] Relocated ROM from {current_folder_name}/ to {target_core_folder}/: {info['filename']}")
                    except Exception as move_ex:
                        logger.debug(f"[{SELF_ID}] ROM relocate error ({info['filename']}): {move_ex}")

                if gid not in existing_games:
                    self._db_execute(
                        """INSERT OR REPLACE INTO games (id, filename, file_path, title, game_code, maker_code, core, platform, size_bytes, mtime, added_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            gid,
                            info["filename"],
                            curr_file_path,
                            clean_title,
                            rom_info["game_code"],
                            rom_info["maker_code"],
                            rom_info["core"],
                            rom_info["platform"],
                            info["size_bytes"],
                            info["mtime"],
                            now_str,
                        ),
                    )
                    covers_to_fetch.append((
                        gid,
                        rom_info.get("core") or rom_info.get("platform"),
                        info["filename"],
                        curr_file_path,
                        mapped_header or clean_title
                    ))
                else:
                    self._db_execute(
                        "UPDATE games SET file_path = ?, size_bytes = ?, mtime = ?, core = ?, platform = ?, title = ? WHERE id = ?",
                        (curr_file_path, info["size_bytes"], info["mtime"], rom_info["core"], rom_info["platform"], clean_title, gid),
                    )
                    existing_entry = existing_games.get(gid)
                    if not new_only and (not existing_entry or not existing_entry.get("cover_path") or not os.path.exists(existing_entry["cover_path"])):
                        covers_to_fetch.append((
                            gid,
                            rom_info.get("core") or rom_info.get("platform"),
                            info["filename"],
                            curr_file_path,
                            mapped_header or clean_title
                        ))

        # 삭제된 게임 정리
        for gid in existing_games:
            if gid not in found_files:
                self._db_execute("DELETE FROM games WHERE id = ?", (gid,))
                self._db_execute("DELETE FROM user_game_data WHERE game_id = ?", (gid,))

        # 누락된 커버 이미지를 비동기 백그라운드 스레드에서 다운로드
        if covers_to_fetch:
            def _async_cover_worker(fetch_list):
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as cover_exec:
                    def _fetch_one(item):
                        g_id, core_p, fname, fpath, raw_t = item
                        try:
                            self._auto_fetch_and_save_cover(g_id, core_p, fname, file_path=fpath, raw_title=raw_t)
                        except Exception:
                            pass
                    list(cover_exec.map(_fetch_one, fetch_list))

            threading.Thread(target=_async_cover_worker, args=(covers_to_fetch,), daemon=True).start()

        return True

    def _get_setting(self, key, default=""):
        """설정값 조회: 게임북 전용 로컬 SQLite DB(settings 테이블)에서 조회합니다."""
        rows = self._db_query("SELECT value FROM settings WHERE key = ?", (key,))
        if rows:
            return rows[0]["value"]
        return default

    def _set_setting(self, key, value):
        """설정값 저장: 게임북 전용 로컬 SQLite DB(settings 테이블)에 저장합니다."""
        self._db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

    # ------------------------------------------------------------------
    # 라우트 동적 등록 (Werkzeug Rule 기반)
    # ------------------------------------------------------------------
    def _ensure_routes(self):
        try:
            from flask import current_app

            if not current_app:
                return
            app = current_app._get_current_object()
            self._do_register_routes(app)
        except Exception as e:
            logger.error(f"[{SELF_ID}] Ensure routes error: {e}")

    def _do_register_routes(self, app):
        with _ROUTES_LOCK:
            app_id = id(app)
            if app_id in _REGISTERED_APPS:
                return

            try:
                from werkzeug.routing import Rule

                routes = {
                    "gba_rom_stream": (f"{ROUTE_BASE}/rom/<game_id>", ["GET", "HEAD"]),
                    "gba_rom_named": (f"{ROUTE_BASE}/rom/<game_id>/<path:filename>", ["GET", "HEAD"]),
                    "gba_bios_stream": (f"{ROUTE_BASE}/bios/<path:filename>", ["GET", "HEAD"]),
                    "gba_save_file": (f"{ROUTE_BASE}/save/<game_id>", ["GET", "POST", "HEAD"]),
                    "gba_state_default": (f"{ROUTE_BASE}/state/<game_id>", ["GET", "POST", "HEAD"]),
                    "gba_state_file": (f"{ROUTE_BASE}/state/<game_id>/<int:slot>", ["GET", "POST", "HEAD"]),
                    "gba_cover_file": (f"{ROUTE_BASE}/cover/<game_id>", ["GET", "HEAD"]),
                    "gba_direct_upload": (f"{ROUTE_BASE}/upload", ["POST"]),
                    "gba_homebrew_install": (f"{ROUTE_BASE}/homebrew-install", ["POST"]),
                }

                registered_endpoints = set(rule.endpoint for rule in app.url_map.iter_rules())

                for endpoint, (path, methods) in routes.items():
                    if endpoint == "gba_rom_named":
                        handler_name = "_route_rom_stream"
                    else:
                        handler_name = "_route_" + endpoint.replace("gba_", "")
                    view_func = getattr(self, handler_name, None)
                    if view_func:
                        # 핫리로드 시 항상 최신 인스턴스의 핸들러로 view_functions 갱신
                        app.view_functions[endpoint] = view_func
                        if endpoint not in registered_endpoints:
                            app.url_map.add(Rule(path, endpoint=endpoint, methods=methods))

                if not getattr(app, "_gba_wsgi_patched", False):
                    orig_wsgi = app.wsgi_app

                    def _gba_wsgi(environ, start_response):
                        path = environ.get("PATH_INFO", "")
                        if path.startswith(ROUTE_BASE):
                            try:
                                self._do_register_routes(app)
                            except Exception:
                                pass
                        return orig_wsgi(environ, start_response)

                    app.wsgi_app = _gba_wsgi
                    app._gba_wsgi_patched = True

                _REGISTERED_APPS.add(app_id)
            except Exception as e:
                logger.error(f"[{SELF_ID}] Route register error: {e}")

    # ------------------------------------------------------------------
    # 라우트 핸들러 (ROM 서빙 / 유저별 세이브 다운로드 & 업로드 / 커버아트)
    # ------------------------------------------------------------------
    def _route_rom_stream(self, game_id, filename=None):
        """ROM 바이너리 다운로드 / 스트리밍 (.7z 압축 롬은 내부 롬을 즉시 추출하여 EmulatorJS 호환 스트림으로 전송)"""
        import io
        from flask import Response, abort, request

        rows = self._db_query("SELECT file_path, filename FROM games WHERE id = ?", (game_id,))
        if not rows or not os.path.exists(rows[0]["file_path"]):
            abort(404, "ROM file not found")

        file_path = rows[0]["file_path"]
        actual_filename = filename or rows[0]["filename"]
        ext = os.path.splitext(file_path)[1].lower()

        # .7z 압축 롬 파일인 경우: py7zr을 이용해 내부 롬 파일을 표준 .zip 형식으로 메모리 변환하여 서빙
        if ext == ".7z":
            try:
                import py7zr
                if py7zr.is_7zfile(file_path):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        with py7zr.SevenZipFile(file_path, mode="r") as z7:
                            z7.extract(path=tmpdir)
                        extracted_files = [f for f in os.listdir(tmpdir) if not f.startswith(".")]

                        # 표준 ZIP 아카이브 메모리 생성
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                            for fname in extracted_files:
                                full_ef = os.path.join(tmpdir, fname)
                                if os.path.isfile(full_ef):
                                    zout.write(full_ef, fname)

                        zip_bytes = zip_buffer.getvalue()
                        clean_stem = os.path.splitext(actual_filename)[0]
                        zip_filename = f"{clean_stem}.zip"

                        ascii_fallback = re.sub(r"[^\x20-\x7E]", "_", zip_filename)
                        encoded_filename = urllib.parse.quote(zip_filename)
                        content_disposition = f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'

                        resp = Response(zip_bytes, 200, mimetype="application/zip")
                        resp.headers["Content-Length"] = str(len(zip_bytes))
                        resp.headers["Content-Disposition"] = content_disposition
                        resp.headers["Cache-Control"] = "public, max-age=86400"
                        return resp
            except Exception as e:
                logger.error(f"[{SELF_ID}] 7z on-the-fly zip conversion error: {e}")

        # 일반 롬 및 .zip 파일: 일반 바이너리 스트리밍 (Range 지원)
        file_size = os.path.getsize(file_path)
        ascii_fallback = re.sub(r"[^\x20-\x7E]", "_", actual_filename)
        encoded_filename = urllib.parse.quote(actual_filename)
        content_disposition = f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'

        range_header = request.headers.get("Range", None)
        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                length = end - start + 1

                def _generate():
                    with open(file_path, "rb") as f:
                        f.seek(start)
                        rem = length
                        while rem > 0:
                            chunk = f.read(min(65536, rem))
                            if not chunk:
                                break
                            rem -= len(chunk)
                            yield chunk

                resp = Response(_generate(), 206, mimetype="application/octet-stream", direct_passthrough=True)
                resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                resp.headers["Content-Length"] = str(length)
                resp.headers["Accept-Ranges"] = "bytes"
                resp.headers["Content-Disposition"] = content_disposition
                return resp

        def _full_stream():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk

        resp = Response(_full_stream(), 200, mimetype="application/octet-stream", direct_passthrough=True)
        resp.headers["Content-Length"] = str(file_size)
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Disposition"] = content_disposition
        return resp

    def _route_bios_stream(self, filename):
        """바이오스 및 기판 펌웨어 파일 스트리밍 (/bios/<filename>)"""
        from flask import Response, request

        safe_name = os.path.basename(filename)
        search_dirs = [self._get_bios_dir(), self._get_roms_dir()]
        extra_p = self._get_setting("EXTRA_ROMS_PATH", "").strip()
        if extra_p and os.path.isdir(extra_p):
            search_dirs.append(extra_p)

        target_path = None
        for sdir in search_dirs:
            p = os.path.join(sdir, safe_name)
            if os.path.isfile(p):
                target_path = p
                break

        if not target_path:
            for sdir in search_dirs:
                if os.path.exists(sdir):
                    try:
                        for f in os.listdir(sdir):
                            if f.lower() == safe_name.lower():
                                target_path = os.path.join(sdir, f)
                                break
                    except Exception:
                        pass
                    if target_path:
                        break

        if not target_path or not os.path.isfile(target_path):
            return Response("BIOS file not found", 404)

        file_size = os.path.getsize(target_path)
        content_disposition = _build_content_disposition(os.path.basename(target_path))

        range_header = request.headers.get("Range", None)
        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                length = end - start + 1

                def _generate():
                    with open(target_path, "rb") as f:
                        f.seek(start)
                        rem = length
                        while rem > 0:
                            chunk = f.read(min(65536, rem))
                            if not chunk:
                                break
                            rem -= len(chunk)
                            yield chunk

                resp = Response(_generate(), 206, mimetype="application/octet-stream", direct_passthrough=True)
                resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                resp.headers["Content-Length"] = str(length)
                resp.headers["Accept-Ranges"] = "bytes"
                resp.headers["Content-Disposition"] = content_disposition
                return resp

        def _full_stream():
            with open(target_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk

        resp = Response(_full_stream(), 200, mimetype="application/octet-stream", direct_passthrough=True)
        resp.headers["Content-Length"] = str(file_size)
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Disposition"] = content_disposition
        return resp

    def _route_save_file(self, game_id):
        """유저별 배터리 세이브 파일 (.sav) 다운로드 (GET) 및 업로드 (POST)"""
        from flask import Response, jsonify, request

        user_id = _get_current_user_id()
        user_saves_dir = self._get_user_saves_dir(user_id)
        save_path = os.path.join(user_saves_dir, f"{game_id}.sav")

        if request.method == "POST":
            try:
                save_data = request.get_data()
                if not save_data:
                    f = request.files.get("save")
                    if f:
                        save_data = f.read()

                if save_data:
                    with open(save_path, "wb") as sf:
                        sf.write(save_data)
                    now_str = _get_kst_now_str()
                    self._db_execute(
                        """INSERT INTO user_game_data (user_id, game_id, last_played_at, play_count)
                           VALUES (?, ?, ?, 1)
                           ON CONFLICT(user_id, game_id) DO UPDATE SET
                           last_played_at = excluded.last_played_at""",
                        (user_id, game_id, now_str),
                    )
                    return jsonify({
                        "success": True,
                        "message": f"유저 #{user_id}의 배터리 세이브가 성공적으로 저장되었습니다.",
                        "user_id": user_id,
                        "size": len(save_data),
                    })
                return jsonify({"success": False, "error": "전송된 세이브 데이터가 없습니다."}), 400
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # GET: 세이브 파일 전송
        if not os.path.exists(save_path):
            return Response(b"", 200, mimetype="application/octet-stream")

        file_size = os.path.getsize(save_path)
        with open(save_path, "rb") as sf:
            content = sf.read()

        resp = Response(content, 200, mimetype="application/octet-stream")
        resp.headers["Content-Length"] = str(file_size)
        resp.headers["Content-Disposition"] = f'attachment; filename="{game_id}.sav"'
        return resp

    def _route_state_default(self, game_id):
        """기본 스냅샷 상태 (/state/<game_id>) -> slot 1로 처리"""
        return self._route_state_file(game_id, slot=1)

    def _route_state_file(self, game_id, slot=1):
        """유저별 실시간 스냅샷 상태 (.state) 다운로드 (GET) 및 업로드 (POST)"""
        from flask import Response, jsonify, request

        user_id = _get_current_user_id()
        user_saves_dir = self._get_user_saves_dir(user_id)
        state_path = os.path.join(user_saves_dir, f"{game_id}_slot{slot}.state")
        default_state_path = os.path.join(user_saves_dir, f"{game_id}.state")

        if request.method == "POST":
            try:
                data = request.get_data()
                if not data:
                    f = request.files.get("state")
                    if f:
                        data = f.read()
                if data:
                    with open(state_path, "wb") as sf:
                        sf.write(data)
                    with open(default_state_path, "wb") as dsf:
                        dsf.write(data)

                    now_str = _get_kst_now_str()
                    self._db_execute(
                        """INSERT INTO user_game_data (user_id, game_id, last_played_at, play_count)
                           VALUES (?, ?, ?, 1)
                           ON CONFLICT(user_id, game_id) DO UPDATE SET
                           last_played_at = excluded.last_played_at""",
                        (user_id, game_id, now_str),
                    )

                    return jsonify({
                        "success": True,
                        "message": f"유저 #{user_id}의 실시간 상태(스냅샷)가 저장되었습니다.",
                        "user_id": user_id,
                        "slot": slot,
                        "size": len(data),
                    })
                return jsonify({"success": False, "error": "전송된 상태 데이터가 없습니다."}), 400
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # GET: 스냅샷 파일 전송 (slot 파일 우선, 없으면 default)
        target_path = state_path if os.path.exists(state_path) else default_state_path
        if not os.path.exists(target_path):
            return Response(b"", 200, mimetype="application/octet-stream")

        with open(target_path, "rb") as sf:
            content = sf.read()

        resp = Response(content, 200, mimetype="application/octet-stream")
        resp.headers["Content-Length"] = str(len(content))
        resp.headers["Content-Disposition"] = f'attachment; filename="{game_id}.state"'
        return resp

    def _route_cover_file(self, game_id):
        """커버 이미지 서빙"""
        from flask import Response, abort

        rows = self._db_query("SELECT cover_path FROM games WHERE id = ?", (game_id,))
        if rows and rows[0]["cover_path"] and os.path.exists(rows[0]["cover_path"]):
            cover_path = rows[0]["cover_path"]
        else:
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                p = os.path.join(self._get_covers_dir(), f"{game_id}{ext}")
                if os.path.exists(p):
                    cover_path = p
                    break
            else:
                abort(404, "Cover image not found")

        ext = os.path.splitext(cover_path)[1].lower().replace(".", "")
        mime = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/webp"

        with open(cover_path, "rb") as f:
            data = f.read()

        resp = Response(data, 200, mimetype=mime)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    def _route_direct_upload(self):
        """ROM 파일 및 커버 아트 업로드 핸들러"""
        from flask import jsonify, request

        if "file" not in request.files:
            return jsonify({"success": False, "error": "업로드할 파일이 전송되지 않았습니다."}), 400

        file = request.files["file"]
        if not file.filename:
            return jsonify({"success": False, "error": "파일명이 비어있습니다."}), 400

        # 업로드 타입 확인 (rom | bios | cover)
        upload_type = request.form.get("type", "rom").lower()

        if upload_type in ("rom", "bios") and not _is_current_user_admin():
            return jsonify({"success": False, "error": "관리자만 ROM 및 바이오스를 업로드할 수 있습니다."}), 403

        raw_filename = file.filename
        safe_filename = re.sub(r"[^\w\.\-\(\) ]", "_", raw_filename)
        ext = os.path.splitext(safe_filename)[1].lower()

        allowed_rom_exts = set(SUPPORTED_SYSTEMS.keys()) | {".zip", ".7z", ".bin", ".rom"}
        allowed_img_exts = {".png", ".jpg", ".jpeg", ".webp"}

        if upload_type == "bios":
            # 바이오스 전용 업로드: bios 디렉토리에 저장
            dest_dir = self._get_bios_dir()
            dest_path = os.path.join(dest_dir, safe_filename)
            file.save(dest_path)
            return jsonify({
                "success": True,
                "message": f"✅ 시스템 바이오스 '{safe_filename}' 등록이 완료되었습니다.",
                "type": "bios",
            })

        if ext in allowed_rom_exts or _is_bios_file(safe_filename):
            is_bios = _is_bios_file(safe_filename)

            if is_bios:
                # 바이오스 파일이 감지된 경우 자동으로 bios 폴더로 직행 저장
                bios_dir = self._get_bios_dir()
                bios_dest_path = os.path.join(bios_dir, safe_filename)
                file.save(bios_dest_path)
                return jsonify({
                    "success": True,
                    "message": f"✅ 시스템 바이오스 '{safe_filename}'이(가) 바이오스 전용 저장소에 등록되었습니다.",
                    "type": "bios",
                })

            # 사용자 설정 추가 ROM 경로(EXTRA_ROMS_PATH)가 있으면 해당 폴더에 우선 업로드
            custom_roms_path = self._get_setting("EXTRA_ROMS_PATH", "").strip()
            if custom_roms_path and os.path.isdir(custom_roms_path):
                dest_dir = custom_roms_path
            else:
                dest_dir = self._get_roms_dir()

            temp_dest = os.path.join(dest_dir, f".temp_upload_{safe_filename}")
            file.save(temp_dest)

            # 롬 파일 유효성 및 기종 판별
            rom_info = _detect_rom_info(temp_dest)
            if rom_info.get("platform") == "_skip_":
                if os.path.exists(temp_dest):
                    os.remove(temp_dest)
                return jsonify({
                    "success": False,
                    "error": f"지원되지 않거나 유효하지 않은 롬 파일({safe_filename})입니다. 지원 기종(SFC, GBA, NES, GB, MD, NDS, N64, PS1, PSP, Arcade 등)을 확인해 주세요.",
                }), 400

            # 코어/시스템 이름별 하위 폴더 결정 (예: snes, gba, nes, segaMD, psx, arcade 등)
            core_name = rom_info.get("core") or rom_info.get("platform") or "other"
            core_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(core_name).strip()).lower() or "other"
            target_sub_dir = os.path.join(dest_dir, core_name)
            os.makedirs(target_sub_dir, exist_ok=True)

            base_n, ext_n = os.path.splitext(safe_filename)
            dest_path = os.path.join(target_sub_dir, safe_filename)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(target_sub_dir, f"{base_n}_{counter}{ext_n}")
                counter += 1

            shutil.move(temp_dest, dest_path)
            self._scan_roms()

            # 아케이드 / 네오지오 롬 감지 시 바이오스 안내 생성
            notice = None
            if rom_info.get("core") == "arcade" or rom_info.get("platform") == "Arcade":
                has_neogeo = os.path.exists(os.path.join(dest_dir, "neogeo.zip")) or os.path.exists(os.path.join(self._get_bios_dir(), "neogeo.zip"))
                if not has_neogeo and any(k in safe_filename.lower() for k in ("kof", "mslug", "samsho", "fatfur", "garou", "neogeo", "snk", "aof", "rbff")):
                    notice = "💡 네오지오(Neo-Geo) 아케이드 롬으로 감지되었습니다. 원활한 구동을 위해 'neogeo.zip' 바이오스 파일도 함께 업로드해 주세요."
                else:
                    notice = f"💡 아케이드({rom_info.get('title') or safe_filename}) 롬입니다. 정상 구동을 위해 필요한 부모 롬이나 바이오스를 확인해 주세요."

            return jsonify({
                "success": True,
                "message": f"[{rom_info.get('platform')}] '{safe_filename}' 롬 업로드가 완료되었습니다.",
                "type": "rom",
                "notice": notice,
            })

        elif ext in allowed_img_exts:
            game_id = request.form.get("game_id", "").strip()
            if not game_id:
                return jsonify({"success": False, "error": "커버 이미지 등록 대상 game_id가 필요합니다."}), 400

            dest_path = os.path.join(self._get_covers_dir(), f"{game_id}{ext}")
            file.save(dest_path)
            self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (dest_path, game_id))
            return jsonify({"success": True, "message": "커버 이미지가 성공적으로 등록되었습니다.", "type": "cover"})

        return jsonify({"success": False, "error": f"지원하지 않는 파일 형식입니다. ({ext})"}), 400

    def _hh_dest_name(self, slug, relpath):
        base = os.path.basename(relpath)
        safe_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(slug or "entry"))[:80]
        safe_base = re.sub(r"[^\w\.\-\(\) ]", "_", base)
        return f"hh_{safe_slug}_{safe_base}"

    def _hh_is_installed(self, slug):
        safe_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(slug or ""))[:80]
        prefix = f"hh_{safe_slug}_"
        roms_dir = self._get_roms_dir()
        try:
            return any(name.startswith(prefix) for name in os.listdir(roms_dir))
        except Exception:
            return False

    def _hh_public_entry(self, entry):
        slug = entry.get("slug") or ""
        rel = _hh_playable_file(entry) or ""
        shots = entry.get("screenshots") or []
        cover = ""
        raw_base = _hh_github_raw_base(entry.get("baserepo"))
        if raw_base and shots:
            shot = _hh_safe_relpath(shots[0])
            if shot:
                cover = f"{raw_base}/entries/{urllib.parse.quote(str(slug))}/{urllib.parse.quote(shot, safe='/')}"
        return {
            "slug": slug,
            "title": entry.get("title") or slug,
            "developer": entry.get("developer") or "",
            "license": entry.get("license") or "홈브류 (라이선스 미기재)",
            "platform": str(entry.get("platform") or "").upper(),
            "typetag": entry.get("typetag") or "",
            "repository": entry.get("repository") or "",
            "filename": os.path.basename(rel),
            "cover_url": cover,
            "installed": self._hh_is_installed(slug),
        }

    def _hh_search(self, title="", platform="", page=1):
        params = {
            "page": str(max(1, int(page or 1))),
            "page_elements": "10",
            "order_by": "title",
            "sort": "asc",
        }
        title = str(title or "").strip()
        platform = str(platform or "").strip().upper()
        if title:
            params["title"] = title
        if platform in HH_ALLOWED_PLATFORMS:
            params["platform"] = platform
        elif not title:
            params["platform"] = "GB"
        qs = urllib.parse.urlencode(params)
        raw, _ctype = _hh_http_get(f"{HH_API}/search?{qs}")
        payload = json.loads(raw.decode("utf-8"))
        entries = []
        for entry in payload.get("entries") or []:
            if _hh_entry_allowed(entry):
                entries.append(self._hh_public_entry(entry))
        return {
            "success": True,
            "entries": entries,
            "results": payload.get("results") or len(entries),
            "page_current": payload.get("page_current") or 1,
            "page_total": payload.get("page_total") or 1,
            "source": "Homebrew Hub (hh.gbdev.io)",
            "note": "GB/GBC/GBA/NES 홈브류·데모만 표시합니다. 시판 ROM·ROM 핵은 등록하지 않습니다.",
        }

    def _hh_install(self, slug):
        slug = str(slug or "").strip()
        if not slug or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,120}$", slug):
            return False, "잘못된 항목 ID입니다."
        raw, _ctype = _hh_http_get(f"{HH_API}/entry/{urllib.parse.quote(slug)}.json")
        entry = json.loads(raw.decode("utf-8"))
        if not _hh_entry_allowed(entry):
            return False, "홈브류·퍼블릭 배포 항목이 아니어서 등록할 수 없습니다."
        rel = _hh_playable_file(entry)
        raw_base = _hh_github_raw_base(entry.get("baserepo"))
        file_url = f"{raw_base}/entries/{urllib.parse.quote(slug)}/{urllib.parse.quote(rel, safe='/')}"
        dest_name = self._hh_dest_name(slug, rel)
        dest_path = os.path.join(self._get_roms_dir(), dest_name)
        if os.path.exists(dest_path):
            self._scan_roms()
            return True, f"이미 등록되어 있습니다: {entry.get('title') or slug}"
        data, _ct = _hh_http_get(file_url, timeout=60)
        if not data or len(data) < 256:
            return False, "ROM 파일이 비어 있거나 너무 작습니다."
        if len(data) > HH_MAX_ROM_BYTES:
            return False, "ROM이 너무 커서 등록하지 않았습니다."
        tmp_path = dest_path + ".part"
        with open(tmp_path, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, dest_path)
        self._scan_roms()
        rows = self._db_query("SELECT id FROM games WHERE filename = ?", (dest_name,))
        game_id = rows[0]["id"] if rows else None
        shots = entry.get("screenshots") or []
        shot = _hh_safe_relpath(shots[0]) if shots else None
        if game_id and shot:
            try:
                cover_bytes, _cover_ct = _hh_http_get(
                    f"{raw_base}/entries/{urllib.parse.quote(slug)}/{urllib.parse.quote(shot, safe='/')}",
                    timeout=20,
                )
                ext = os.path.splitext(shot)[1].lower()
                if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
                    ext = ".png"
                cover_path = os.path.join(self._get_covers_dir(), f"{game_id}{ext}")
                with open(cover_path, "wb") as cf:
                    cf.write(cover_bytes)
                self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (cover_path, game_id))
            except Exception as e:
                logger.debug(f"[{SELF_ID}] Homebrew cover skip: {e}")
        return True, f"'{entry.get('title') or slug}' 을(를) 라이브러리에 등록했습니다."

    def _route_homebrew_install(self):
        """Homebrew Hub에서 배포가 허용된 홈브류 ROM만 라이브러리에 등록한다."""
        from flask import jsonify, request

        if not _is_current_user_admin():
            return jsonify({"success": False, "error": "관리자만 홈브류를 등록할 수 있습니다."}), 403
        payload = request.get_json(silent=True) or {}
        slug = str(payload.get("slug") or request.form.get("slug") or "").strip()
        try:
            ok, message = self._hh_install(slug)
            status = 200 if ok else 400
            return jsonify({"success": ok, "message": message if ok else None, "error": None if ok else message}), status
        except urllib.error.HTTPError as e:
            return jsonify({"success": False, "error": f"파일을 받지 못했습니다 ({e.code})"}), 400
        except Exception as e:
            logger.error(f"[{SELF_ID}] Homebrew install error: {e}")
            return jsonify({"success": False, "error": "홈브류 등록 중 오류가 발생했습니다."}), 500

    # ------------------------------------------------------------------
    # 플러그인 대시보드 API (get_dashboard_data)
    # ------------------------------------------------------------------
    def get_dashboard_data(self, db_type, limit=10):
        """북오아시스 카테고리 진입 시 및 프론트엔드 비동기 요청 처리"""
        self._ensure_routes()
        from flask import request

        user_id = _get_current_user_id()
        action = request.args.get("action", "list_games").strip()

        try:
            if action == "list_games":
                # DB에 게임 데이터가 한 건도 없을 때만 최초 1회 자동 초기 스캔
                game_count_row = self._db_query("SELECT COUNT(*) AS cnt FROM games")
                if not game_count_row or game_count_row[0]["cnt"] == 0:
                    self._scan_roms()

                games = self._db_query(
                    """SELECT g.id, g.filename, g.file_path, g.title, g.game_code, g.maker_code,
                              g.size_bytes, g.added_at, g.cover_path, g.core, g.platform,
                              COALESCE(u.is_favorite, 0) AS is_favorite,
                              u.last_played_at,
                              COALESCE(u.play_count, 0) AS play_count
                       FROM games g
                       LEFT JOIN user_game_data u ON g.id = u.game_id AND u.user_id = ?
                       ORDER BY is_favorite DESC, u.last_played_at DESC, added_at DESC""",
                    (user_id,),
                )

                user_saves_dir = self._get_user_saves_dir(user_id)

                for g in games:
                    save_path = os.path.join(user_saves_dir, f"{g['id']}.sav")
                    state_path = os.path.join(user_saves_dir, f"{g['id']}.state")
                    state_slot1 = os.path.join(user_saves_dir, f"{g['id']}_slot1.state")

                    has_sav = os.path.exists(save_path) and os.path.getsize(save_path) > 0
                    has_state = (os.path.exists(state_path) and os.path.getsize(state_path) > 0) or (os.path.exists(state_slot1) and os.path.getsize(state_slot1) > 0)

                    g["has_save"] = 1 if (has_sav or has_state) else 0
                    g["has_state"] = 1 if has_state else 0
                    url_fname = g["filename"]
                    if url_fname.lower().endswith(".7z"):
                        url_fname = os.path.splitext(url_fname)[0] + ".zip"
                    g["rom_url"] = f"{ROUTE_BASE}/rom/{g['id']}/{urllib.parse.quote(url_fname)}"
                    g["save_url"] = f"{ROUTE_BASE}/save/{g['id']}?user_id={user_id}"
                    g["state_url"] = f"{ROUTE_BASE}/state/{g['id']}?user_id={user_id}"
                    g["cover_url"] = f"{ROUTE_BASE}/cover/{g['id']}"

                available_bios = []
                bios_dir = self._get_bios_dir()
                if os.path.exists(bios_dir):
                    try:
                        for f in os.listdir(bios_dir):
                            if not f.startswith("."):
                                available_bios.append(f.lower())
                    except Exception:
                        pass

                return {
                    "success": True,
                    "games": games,
                    "total_count": len(games),
                    "available_bios": sorted(list(set(available_bios))),
                    "user_id": user_id,
                    "is_admin": _is_current_user_admin(),
                    "config": {
                        "cloud_save_enabled": str(self._get_setting("CLOUD_SAVE_ENABLED", "1")).lower() in ("1", "true", "yes", "on"),
                        "auto_save_interval_sec": int(self._get_setting("AUTO_SAVE_INTERVAL_SEC", "60")),
                        "extra_roms_path": str(self._get_setting("EXTRA_ROMS_PATH", "") or "").strip(),
                        "covers_path": str(self._get_setting("COVERS_PATH", "") or "").strip(),
                        "bios_path": str(self._get_setting("BIOS_PATH", "") or "").strip(),
                        "ss_devid": str(self._get_setting("SS_DEVID", "") or "").strip(),
                        "ss_devpassword": str(self._get_setting("SS_DEVPASSWORD", "") or "").strip(),
                        "ss_user": str(self._get_setting("SS_USER", "") or "").strip(),
                        "ss_password": str(self._get_setting("SS_PASSWORD", "") or "").strip(),
                        "igdb_client_id": str(self._get_setting("IGDB_CLIENT_ID", "") or "").strip(),
                        "igdb_client_secret": str(self._get_setting("IGDB_CLIENT_SECRET", "") or "").strip(),
                    },
                }

            elif action == "check_game":
                game_id = request.args.get("game_id", "").strip()
                if not game_id:
                    return {"success": False, "error": "game_id 파라미터가 필요합니다."}
                res = self._check_and_update_game(game_id)
                return {"success": True, "result": res}

            elif action == "get_cover_migration_candidates":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}
                target_dir = request.args.get("target_dir", "").strip()
                if not target_dir:
                    return {"success": False, "error": "target_dir 파라미터가 필요합니다."}
                target_dir = os.path.abspath(target_dir)

                old_default_dir = os.path.abspath(os.path.join(self._get_data_dir(), "covers"))
                files_to_migrate = []

                # 1. covers/ 디렉터리 내 물리 파일
                if os.path.exists(old_default_dir) and old_default_dir != target_dir:
                    try:
                        for f in os.listdir(old_default_dir):
                            if f.startswith("."):
                                continue
                            full_p = os.path.join(old_default_dir, f)
                            if os.path.isfile(full_p):
                                files_to_migrate.append({"type": "file", "path": full_p, "name": f})
                    except Exception as e:
                        logger.error(f"[{SELF_ID}] List covers error: {e}")

                # 2. DB cover_path 중 아직 새 폴더가 아닌 항목들
                db_rows = self._db_query("SELECT id, cover_path FROM games WHERE cover_path IS NOT NULL AND cover_path != ''")
                for r in db_rows:
                    c_path = r["cover_path"]
                    if c_path:
                        c_dir = os.path.abspath(os.path.dirname(c_path))
                        if c_dir != target_dir:
                            files_to_migrate.append({"type": "db_row", "game_id": r["id"], "path": c_path, "name": os.path.basename(c_path)})

                # 중복 제거 (path 기준)
                seen_paths = set()
                unique_list = []
                for item in files_to_migrate:
                    if item.get("path") and item["path"] not in seen_paths:
                        seen_paths.add(item["path"])
                        unique_list.append(item)

                return {"success": True, "total": len(unique_list), "items": unique_list, "target_dir": target_dir}

            elif action == "get_bios_migration_candidates":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}
                target_dir = request.args.get("target_dir", "").strip()
                if not target_dir:
                    return {"success": False, "error": "target_dir 파라미터가 필요합니다."}
                target_dir = os.path.abspath(target_dir)

                old_default_dir = os.path.abspath(os.path.join(self._get_data_dir(), "bios"))
                files_to_migrate = []

                if os.path.exists(old_default_dir) and old_default_dir != target_dir:
                    try:
                        for f in os.listdir(old_default_dir):
                            if f.startswith("."):
                                continue
                            full_p = os.path.join(old_default_dir, f)
                            if os.path.isfile(full_p):
                                files_to_migrate.append({"type": "file", "path": full_p, "name": f})
                    except Exception as e:
                        logger.error(f"[{SELF_ID}] List bios error: {e}")

                seen_paths = set()
                unique_list = []
                for item in files_to_migrate:
                    if item.get("path") and item["path"] not in seen_paths:
                        seen_paths.add(item["path"])
                        unique_list.append(item)

                return {"success": True, "total": len(unique_list), "items": unique_list, "target_dir": target_dir}

            elif action == "migrate_bios_batch":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}
                json_data = request.get_json(silent=True) or {}
                target_dir = json_data.get("target_dir") or request.form.get("target_dir") or request.args.get("target_dir") or ""
                target_dir = os.path.abspath(target_dir.strip()) if target_dir else ""
                if not target_dir:
                    return {"success": False, "error": "target_dir 파라미터가 필요합니다."}

                os.makedirs(target_dir, exist_ok=True)
                items = json_data.get("items", [])
                moved_count = 0

                for it in items:
                    src_p = it.get("path")
                    fname = it.get("name") or (os.path.basename(src_p) if src_p else "")

                    if src_p and os.path.exists(src_p):
                        dst_p = os.path.join(target_dir, fname)
                        if src_p != dst_p:
                            try:
                                if not os.path.exists(dst_p):
                                    shutil.move(src_p, dst_p)
                                else:
                                    os.remove(src_p)
                                moved_count += 1
                            except Exception as e:
                                logger.error(f"[{SELF_ID}] Move bios batch error ({fname}): {e}")

                return {"success": True, "moved_count": moved_count}

            elif action == "migrate_cover_batch":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}
                json_data = request.get_json(silent=True) or {}
                target_dir = json_data.get("target_dir") or request.form.get("target_dir") or request.args.get("target_dir") or ""
                target_dir = os.path.abspath(target_dir.strip()) if target_dir else ""
                if not target_dir:
                    return {"success": False, "error": "target_dir 파라미터가 필요합니다."}

                os.makedirs(target_dir, exist_ok=True)
                items = json_data.get("items", [])
                moved_count = 0

                for it in items:
                    src_p = it.get("path")
                    fname = it.get("name") or (os.path.basename(src_p) if src_p else "")
                    game_id = it.get("game_id")

                    if src_p and os.path.exists(src_p):
                        dst_p = os.path.join(target_dir, fname)
                        if src_p != dst_p:
                            try:
                                if not os.path.exists(dst_p):
                                    shutil.move(src_p, dst_p)
                                else:
                                    os.remove(src_p)
                                moved_count += 1
                            except Exception as e:
                                logger.error(f"[{SELF_ID}] Move cover batch error ({fname}): {e}")

                        # DB 갱신
                        if game_id:
                            self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (dst_p, game_id))
                        else:
                            # filename에서 game_id 역추적
                            stem = os.path.splitext(fname)[0]
                            self._db_execute("UPDATE games SET cover_path = ? WHERE id = ? OR cover_path = ?", (dst_p, stem, src_p))

                return {"success": True, "moved_count": moved_count}

            elif action == "scan_new_roms":
                self._scan_roms(new_only=True)
                return {"success": True, "message": "새로운 ROM 파일 스캔 및 등록이 완료되었습니다."}

            elif action == "scan_roms":
                self._scan_roms()
                return {"success": True, "message": "ROM 디스크 스캔 및 DB 동기화가 완료되었습니다."}

            elif action == "homebrew_search":
                title = request.args.get("q", "")
                platform = request.args.get("platform", "")
                page = request.args.get("page", "1")
                try:
                    return self._hh_search(title=title, platform=platform, page=page)
                except urllib.error.HTTPError as e:
                    return {"success": False, "error": f"Homebrew Hub 응답 오류 ({e.code})"}
                except Exception as e:
                    logger.error(f"[{SELF_ID}] Homebrew search error: {e}")
                    return {"success": False, "error": "Homebrew Hub 검색에 실패했습니다."}

            elif action == "record_play":
                game_id = request.args.get("game_id", "")
                if game_id:
                    now_str = _get_kst_now_str()
                    self._db_execute(
                        """INSERT INTO user_game_data (user_id, game_id, last_played_at, play_count)
                           VALUES (?, ?, ?, 1)
                           ON CONFLICT(user_id, game_id) DO UPDATE SET
                           last_played_at = excluded.last_played_at,
                           play_count = play_count + 1""",
                        (user_id, game_id, now_str),
                    )
                    return {"success": True, "last_played_at": now_str, "user_id": user_id}
                return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}

            elif action == "toggle_favorite":
                game_id = request.args.get("game_id", "")
                if game_id:
                    rows = self._db_query(
                        "SELECT is_favorite FROM user_game_data WHERE user_id = ? AND game_id = ?",
                        (user_id, game_id),
                    )
                    if rows:
                        new_fav = 0 if rows[0]["is_favorite"] else 1
                        self._db_execute(
                            "UPDATE user_game_data SET is_favorite = ? WHERE user_id = ? AND game_id = ?",
                            (new_fav, user_id, game_id),
                        )
                    else:
                        new_fav = 1
                        self._db_execute(
                            "INSERT INTO user_game_data (user_id, game_id, is_favorite) VALUES (?, ?, ?)",
                            (user_id, game_id, new_fav),
                        )
                    return {"success": True, "is_favorite": new_fav, "user_id": user_id}
            elif action == "reset_game_save":
                game_id = request.args.get("game_id", "")
                if game_id:
                    user_saves_dir = self._get_user_saves_dir(user_id)
                    deleted_files = []
                    for f in (f"{game_id}.sav", f"{game_id}.state", f"{game_id}_slot1.state", f"{game_id}_slot2.state", f"{game_id}_slot3.state"):
                        p = os.path.join(user_saves_dir, f)
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                                deleted_files.append(f)
                            except Exception:
                                pass
                    return {"success": True, "message": "세이브 데이터가 성공적으로 초기화되었습니다.", "deleted": deleted_files}
                return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}

            elif action == "delete_game":
                game_id = request.args.get("game_id", "")
                if game_id:
                    rows = self._db_query("SELECT file_path FROM games WHERE id = ?", (game_id,))
                    if rows and rows[0]["file_path"] and os.path.exists(rows[0]["file_path"]):
                        try:
                            if rows[0]["file_path"].startswith(self._get_roms_dir()):
                                os.remove(rows[0]["file_path"])
                        except Exception:
                            pass

                    user_saves_dir = self._get_user_saves_dir(user_id)
                    for f in (f"{game_id}.sav", f"{game_id}.state", f"{game_id}_slot1.state", f"{game_id}_slot2.state", f"{game_id}_slot3.state"):
                        p = os.path.join(user_saves_dir, f)
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except Exception:
                                pass

                    self._db_execute("DELETE FROM games WHERE id = ?", (game_id,))
                    self._db_execute("DELETE FROM user_game_data WHERE game_id = ?", (game_id,))
                    return {"success": True, "message": "게임이 성공적으로 삭제되었습니다."}
                return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}

            elif action == "update_title":
                game_id = request.args.get("game_id", "")
                new_title = request.args.get("title", "").strip()
                if game_id and new_title:
                    self._db_execute("UPDATE games SET title = ? WHERE id = ?", (new_title, game_id))
                    return {"success": True, "title": new_title}
                return {"success": False, "error": "올바르지 않은 game_id 또는 제목입니다."}

            elif action == "save_settings":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자(admin) 권한이 있는 사용자만 설정을 변경할 수 있습니다."}

                # args, form, json 지원
                json_data = request.get_json(silent=True) or {}
                def _get_val(key, default=""):
                    return request.args.get(key) or request.form.get(key) or json_data.get(key) or default

                extra_path = str(_get_val("extra_roms_path", "")).strip()
                covers_path = str(_get_val("covers_path", "")).strip()
                bios_path = str(_get_val("bios_path", "")).strip()
                cloud_save_raw = _get_val("cloud_save_enabled", "1")
                interval_raw = _get_val("auto_save_interval_sec", "60")

                ss_devid = str(_get_val("ss_devid", "")).strip()
                ss_devpassword = str(_get_val("ss_devpassword", "")).strip()
                ss_user = str(_get_val("ss_user", "")).strip()
                ss_password = str(_get_val("ss_password", "")).strip()
                igdb_client_id = str(_get_val("igdb_client_id", "")).strip()
                igdb_client_secret = str(_get_val("igdb_client_secret", "")).strip()

                cloud_save = True if str(cloud_save_raw).lower() in ("1", "true", "yes", "on") else False
                try:
                    interval = int(interval_raw)
                except Exception:
                    interval = 60

                prev_covers_path = str(self._get_setting("COVERS_PATH", "")).strip()
                prev_bios_path = str(self._get_setting("BIOS_PATH", "")).strip()

                self._set_setting("EXTRA_ROMS_PATH", extra_path)
                self._set_setting("COVERS_PATH", covers_path)
                self._set_setting("BIOS_PATH", bios_path)
                self._set_setting("CLOUD_SAVE_ENABLED", cloud_save)
                self._set_setting("AUTO_SAVE_INTERVAL_SEC", interval)
                self._set_setting("SS_DEVID", ss_devid)
                self._set_setting("SS_DEVPASSWORD", ss_devpassword)
                self._set_setting("SS_USER", ss_user)
                self._set_setting("SS_PASSWORD", ss_password)
                self._set_setting("IGDB_CLIENT_ID", igdb_client_id)
                self._set_setting("IGDB_CLIENT_SECRET", igdb_client_secret)

                # 커버 이미지 폴더가 새로 지정되었거나 변경되었을 경우 기존 커버 파일들을 새 위치로 마이그레이션
                if covers_path and covers_path != prev_covers_path:
                    try:
                        self._migrate_covers_to_custom_dir(covers_path)
                    except Exception as e:
                        logger.error(f"[{SELF_ID}] Cover migration during save_settings error: {e}")

                # 바이오스 폴더가 새로 지정되었거나 변경되었을 경우 기존 바이오스 파일들을 새 위치로 마이그레이션
                if bios_path and bios_path != prev_bios_path:
                    try:
                        self._migrate_bios_to_custom_dir(bios_path)
                    except Exception as e:
                        logger.error(f"[{SELF_ID}] Bios migration during save_settings error: {e}")

                # ROM 디렉토리 스캔을 백그라운드로 실행하여 저장 응답 타임아웃 방지
                threading.Thread(target=self._scan_roms, daemon=True).start()
                return {"success": True, "message": "설정이 성공적으로 저장되었습니다."}

            elif action == "search_artwork":
                game_id = request.args.get("game_id", "").strip()
                query = request.args.get("q", "").strip()
                rows = self._db_query("SELECT * FROM games WHERE id = ?", (game_id,)) if game_id else []
                game = rows[0] if rows else {}

                plat = (game.get("core") or game.get("platform") or "gba").lower()
                filename = game.get("filename") or ""
                file_path = game.get("file_path") or ""
                title = query or game.get("title") or filename

                # ROM 파일 헤더에서 영문 고유명 추출
                rom_header_title = ""
                mapped_header_title = ""
                if file_path and os.path.exists(file_path):
                    rom_info = _detect_rom_info(file_path)
                    rom_header_title = rom_info.get("title") or ""
                    if rom_header_title:
                        mapped_header_title = KNOWN_N64_NAMES.get(rom_header_title.upper().replace("_", " ").replace("-", " ").strip()) or KNOWN_N64_NAMES.get(rom_header_title.upper()) or rom_header_title

                results = []

                # 1. Libretro CDN 실시간 검색 (ROM 헤더 & 스마트 매핑)
                key = plat
                system_repo = LIBRETRO_SYSTEM_MAP.get(key)
                if not system_repo:
                    for k, v in LIBRETRO_SYSTEM_MAP.items():
                        if k in key:
                            system_repo = v
                            break
                if system_repo:
                    repo_titles = _get_libretro_repo_titles(system_repo)

                    # 후보 검색어 우선순위 큐
                    search_queries = []

                    # 1순위: ROM 헤더에서 감지된 공식 영문명 (가장 정확)
                    if mapped_header_title and mapped_header_title != "64":
                        search_queries.append(mapped_header_title)
                    if rom_header_title and rom_header_title not in ("64", mapped_header_title):
                        search_queries.append(rom_header_title)

                    # 2순위: 한글 게임명에 대한 대표 영문 매핑
                    norm_t = re.sub(r"[^a-zA-Z0-9가-힣]", "", title).lower()
                    if "역전재판" in norm_t or "gyakuten" in norm_t: search_queries.extend(["Gyakuten Saiban", "Phoenix Wright"])
                    elif "메탈슬러그" in norm_t or "metalslug" in norm_t: search_queries.append("Metal Slug")
                    elif "크로노" in norm_t or "chrono" in norm_t: search_queries.append("Chrono Trigger")
                    elif "골든액스" in norm_t or "goldenaxe" in norm_t: search_queries.append("Golden Axe")
                    elif "이상한모자" in norm_t or "minish" in norm_t: search_queries.append("The Minish Cap")
                    elif "마리오카트" in norm_t or "mariokart" in norm_t: search_queries.append("Mario Kart 64" if plat == "n64" else "Mario Kart")
                    elif "슈퍼마리오" in norm_t or "supermario" in norm_t: search_queries.append("Super Mario 64" if plat == "n64" else "Super Mario")
                    elif "스타크래프트" in norm_t or "starcraft" in norm_t: search_queries.append("StarCraft 64" if plat == "n64" else "StarCraft")
                    elif "스타폭스" in norm_t or "starfox" in norm_t: search_queries.append("Star Fox 64" if plat == "n64" else "Star Fox")
                    elif "에반게리온" in norm_t or "evangelion" in norm_t: search_queries.append("Neon Genesis Evangelion")
                    elif "오우거" in norm_t or "ogre" in norm_t: search_queries.append("Ogre Battle 64" if plat == "n64" else "Ogre Battle")
                    elif "시렌" in norm_t or "shiren" in norm_t: search_queries.append("Shiren 2" if plat == "n64" else "Shiren")
                    elif "죄와벌" in norm_t: search_queries.append("Sin and Punishment")
                    elif "페이퍼마리오" in norm_t or "papermario" in norm_t: search_queries.append("Paper Mario")
                    elif "뿌요뿌요" in norm_t or "puyo" in norm_t: search_queries.append("Puyo Puyo Sun 64" if plat == "n64" else "Puyo Puyo")
                    elif "슈퍼로봇" in norm_t: search_queries.append("Super Robot Taisen 64" if plat == "n64" else "Super Robot")
                    elif "컨커" in norm_t: search_queries.append("Conker's Bad Fur Day")
                    elif "무쥬라" in norm_t or "mujura" in norm_t or "majora" in norm_t: search_queries.append("Majora's Mask")
                    elif "오카리나" in norm_t or "ocarina" in norm_t: search_queries.append("Ocarina of Time")
                    elif "젤다" in norm_t or "zelda" in norm_t: search_queries.append("Legend of Zelda")
                    elif "건버드" in norm_t or "gunbird" in norm_t: search_queries.append("Gunbird")

                    # 3순위: 사용자 입력 검색어 (직접 타이핑한 경우)
                    if query and query not in search_queries:
                        search_queries.append(query)

                    # 4순위: 파일명에서 불용어/태그 제거 후 영문 단어 추출
                    if filename:
                        b_orig = _clean_libretro_name(filename)[0]
                        b_clean = re.sub(r"[\(\[\{].*?[\)\]\}]", "", b_orig).strip()
                        if b_clean and b_clean not in search_queries:
                            search_queries.append(b_clean)

                    seen = set()
                    matched_titles = []
                    # 불용어 정의: 단독으로 사용되면 무의미한 단어들
                    stopwords = {"64", "v64", "z64", "n64", "k", "j", "u", "e", "the", "of", "and"}

                    matched_keyword = ""
                    if repo_titles:
                        for sq in search_queries:
                            raw_kws = [w.lower() for w in re.sub(r"[^a-zA-Z0-9\s]", " ", sq).split() if len(w) >= 2]
                            meaningful_kws = [kw for kw in raw_kws if kw not in stopwords]
                            # 유의미한 단어가 없으면 이 쿼리는 검색하지 않음 (예: '마리오 카트 64'에서 64만 남는 경우 방지)
                            if not meaningful_kws:
                                continue

                            step_matched = []
                            for rt in repo_titles:
                                if rt in seen:
                                    continue
                                rt_lower = rt.lower()
                                if all(kw in rt_lower for kw in meaningful_kws):
                                    seen.add(rt)
                                    step_matched.append(rt)
                                    if len(matched_titles) + len(step_matched) >= 16:
                                        break
                            if step_matched:
                                if not matched_keyword:
                                    matched_keyword = sq
                                matched_titles.extend(step_matched)
                            if len(matched_titles) >= 16:
                                break

                    for mt in matched_titles:
                        enc = urllib.parse.quote(f"{mt}.png", safe="")
                        cdn_url = f"{LIBRETRO_CDN_BASE}/{system_repo}/master/Named_Boxarts/{enc}"
                        results.append({
                            "source": "Libretro CDN",
                            "title": mt,
                            "thumb_url": cdn_url,
                            "image_url": cdn_url,
                        })

                actual_query = matched_keyword or query or title
                return {"success": True, "results": results, "query": actual_query}

            elif action == "set_artwork":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자만 커버를 변경할 수 있습니다."}

                game_id = request.args.get("game_id", "").strip()
                image_url = request.args.get("image_url", "").strip()
                if not game_id or not image_url:
                    return {"success": False, "error": "game_id 또는 image_url이 누락되었습니다."}

                try:
                    # GitHub Raw CDN의 Git Symlink 파일 자동 추적 처리 (최대 3회)
                    curr_url = image_url
                    img_bytes = None
                    for _ in range(3):
                        req = urllib.request.Request(curr_url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
                        with urllib.request.urlopen(req, timeout=20) as resp:
                            data = resp.read()

                        # 256바이트 미만이고 .png 텍스트가 포함된 경우 Git Symlink 링크 파일임
                        if len(data) < 256 and b".png" in data:
                            target_filename = data.decode("utf-8", errors="ignore").strip()
                            # URL 경로에서 마지막 파일명을 타깃 파일명으로 치환
                            base_url_dir = curr_url.rsplit("/", 1)[0]
                            curr_url = f"{base_url_dir}/{urllib.parse.quote(target_filename)}"
                            continue

                        img_bytes = data
                        break

                    if not img_bytes or len(img_bytes) < 256:
                        return {"success": False, "error": "유효하지 않은 이미지 파일입니다."}

                    ext = ".png" if (curr_url.lower().endswith(".png") or image_url.lower().endswith(".png")) else ".jpg"
                    dest_path = os.path.join(self._get_covers_dir(), f"{game_id}{ext}")
                    with open(dest_path, "wb") as f:
                        f.write(img_bytes)

                    self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (dest_path, game_id))
                    return {"success": True, "message": "커버 이미지가 성공적으로 변경되었습니다.", "cover_path": dest_path}
                except Exception as e:
                    logger.error(f"[{SELF_ID}] Set artwork error: {e}")
                    return {"success": False, "error": f"이미지 다운로드 실패: {e}"}

            return {"success": False, "error": f"알 수 없는 액션 요청입니다: '{action}'"}

        except Exception as e:
            logger.error(f"[{SELF_ID}] Dashboard data error: {e}")
            return {"success": False, "error": str(e)}

    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, "카테고리 전용 EmulatorJS 에뮬레이터 플러그인입니다."


# Flask 404 Fallback Hook (서버 재시작 후 첫 요청 시에도 라우트를 즉시 자동 바인딩)
from flask import Flask, request
from werkzeug.exceptions import NotFound

if not getattr(Flask, "_gba_404_patched", False):
    _orig_handle_user_exception = Flask.handle_user_exception

    def _gba_handle_user_exception(self, e):
        if isinstance(e, NotFound) and getattr(e, "code", None) == 404:
            try:
                path = request.path
                if path.startswith(ROUTE_BASE):
                    p = BookoasisGamebooksMetadataProvider()
                    p._do_register_routes(self)
                    adapter = self.create_url_adapter(request)
                    endpoint, values = adapter.match(path_info=path, method=request.method)
                    return self.view_functions[endpoint](**values)
            except Exception as ex:
                logger.error(f"[{SELF_ID}] 404 fallback dispatch error: {ex}")
        return _orig_handle_user_exception(self, e)

    Flask.handle_user_exception = _gba_handle_user_exception
    Flask._gba_404_patched = True
