# -*- coding: utf-8 -*-
"""
북오아시스 EmulatorJS 레트로 게임 에뮬레이터 플러그인.
웹어셈블리(WebAssembly) 기반 EmulatorJS 게임 에뮬레이터 플레이어,
ROM 라이브러리 관리자 및 유저별 영속 클라우드 세이브(배터리 세이브 + 실시간 스냅샷) 동기화 플러그인.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

from plugins.metadata.base import BaseMetadataProvider

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
                    # KNOWN_ARCADE_TITLES에 등록된 게임만 아케이드/네오지오로 인정
                    # 등록되지 않은 zip은 MAME 디바이스/펌웨어/바이오스로 간주하여 무시
                    stem = os.path.splitext(os.path.basename(file_path))[0].lower()
                    if stem in KNOWN_ARCADE_TITLES:
                        info["core"] = "arcade"
                        info["platform"] = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"
                        info["title"] = KNOWN_ARCADE_TITLES[stem]
                    else:
                        info["core"] = "_skip_"
                        info["platform"] = "_skip_"
        except Exception as e:
            logger.debug(f"[{SELF_ID}] Zip inspect error: {e}")
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
        """커버 아트 이미지 디렉터리 (../../data/bookoasis_gamebooks/covers/)"""
        covers_dir = os.path.join(self._get_data_dir(), "covers")
        os.makedirs(covers_dir, exist_ok=True)
        return covers_dir

    def _get_bios_dir(self):
        """시스템 바이오스 파일 디렉터리 (../../data/bookoasis_gamebooks/bios/)"""
        bios_dir = os.path.join(self._get_data_dir(), "bios")
        os.makedirs(bios_dir, exist_ok=True)
        return bios_dir

    def _get_db_path(self):
        """설정 및 게임 메타 SQLite DB 경로"""
        return os.path.join(self._get_data_dir(), "gba.db")

    def __init__(self):
        super().__init__()
        self._migrate_old_plugin_data()
        self._init_db()

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
                        added_at TEXT,
                        cover_path TEXT
                    )"""
                )
                for col, ctype in (("core", "TEXT DEFAULT 'gba'"), ("platform", "TEXT DEFAULT 'GBA'")):
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

    # ------------------------------------------------------------------
    # ROM 스캔 및 등록 로직
    # ------------------------------------------------------------------
    def _scan_roms(self):
        """기본 roms 폴더 및 설정된 추가 경로의 ROM을 스캔하여 DB에 동기화합니다."""
        scan_dirs = [self._get_roms_dir()]

        extra_path = self._get_setting("EXTRA_ROMS_PATH", "").strip()
        if extra_path and os.path.isdir(extra_path):
            scan_dirs.append(extra_path)

        allowed_exts = set(SUPPORTED_SYSTEMS.keys()) | {".zip", ".7z"}
        found_files = {}

        for sdir in scan_dirs:
            try:
                for root, _, files in os.walk(sdir):
                    for f in files:
                        if _is_bios_file(f):
                            continue
                        ext = os.path.splitext(f)[1].lower()
                        if ext in allowed_exts:
                            full_p = os.path.join(root, f)
                            try:
                                sz = os.path.getsize(full_p)
                                rel = os.path.relpath(full_p, sdir)
                                gid = _sanitize_id(f"{os.path.basename(sdir)}_{rel}")
                                found_files[gid] = {
                                    "id": gid,
                                    "filename": f,
                                    "file_path": full_p,
                                    "size_bytes": sz,
                                }
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"[{SELF_ID}] Scan dir error ({sdir}): {e}")

        existing_games = {g["id"]: g for g in self._db_query("SELECT * FROM games")}
        now_str = _get_kst_now_str()

        for gid, info in found_files.items():
            rom_info = _detect_rom_info(info["file_path"])
            # MAME 디바이스/펌웨어/바이오스 zip은 게임으로 등록하지 않음
            if rom_info.get("platform") == "_skip_":
                continue
            raw_name = _strip_romm_name_prefix(os.path.splitext(info["filename"])[0])
            stem = raw_name.lower()

            if stem in KNOWN_ARCADE_TITLES:
                clean_title = KNOWN_ARCADE_TITLES[stem]
            else:
                norm_key = re.sub(r"[^a-zA-Z0-9가-힣]", "", raw_name).lower()
                if "gyakuten" in norm_key or "역전재판" in norm_key:
                    clean_title = "역전재판 1"
                elif "metalslugadvance" in norm_key or "메탈슬러그어드밴스" in norm_key:
                    clean_title = "메탈슬러그 어드밴스드"
                elif "chronotrigger" in norm_key or "크로노트리거" in norm_key:
                    clean_title = "크로노 트리거 (Chrono Trigger)"
                elif "goldenaxe" in norm_key or "골든액스" in norm_key:
                    clean_title = "골든 액스 (Golden Axe)"
                elif "minishcap" in norm_key or "이상한모자" in norm_key:
                    clean_title = "젤다의 전설: 이상한 모자"
                else:
                    no_brackets = re.sub(r"[\(\[\{].*?[\)\]\}]", "", raw_name).strip().replace("_", " ").replace("-", " ")
                    clean_title = re.sub(r"\s+", " ", no_brackets).strip() or raw_name

            if gid not in existing_games:
                self._db_execute(
                    """INSERT INTO games (id, filename, file_path, title, game_code, maker_code, core, platform, size_bytes, added_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        gid,
                        info["filename"],
                        info["file_path"],
                        clean_title,
                        rom_info["game_code"],
                        rom_info["maker_code"],
                        rom_info["core"],
                        rom_info["platform"],
                        info["size_bytes"],
                        now_str,
                    ),
                )
            else:
                self._db_execute(
                    "UPDATE games SET file_path = ?, size_bytes = ?, core = ?, platform = ?, title = ? WHERE id = ?",
                    (info["file_path"], info["size_bytes"], rom_info["core"], rom_info["platform"], clean_title, gid),
                )

        for gid in existing_games:
            if gid not in found_files:
                self._db_execute("DELETE FROM games WHERE id = ?", (gid,))
                self._db_execute("DELETE FROM user_game_data WHERE game_id = ?", (gid,))

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

                for endpoint, (path, methods) in routes.items():
                    if endpoint not in app.view_functions:
                        if endpoint == "gba_rom_named":
                            handler_name = "_route_rom_stream"
                        else:
                            handler_name = "_route_" + endpoint.replace("gba_", "")
                        view_func = getattr(self, handler_name, None)
                        if view_func:
                            app.view_functions[endpoint] = view_func
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
        """ROM 바이너리 다운로드 / 스트리밍"""
        from flask import Response, abort, request

        rows = self._db_query("SELECT file_path, filename FROM games WHERE id = ?", (game_id,))
        if not rows or not os.path.exists(rows[0]["file_path"]):
            abort(404, "ROM file not found")

        file_path = rows[0]["file_path"]
        actual_filename = filename or rows[0]["filename"]
        file_size = os.path.getsize(file_path)

        # ASCII 안전 파일명 및 UTF-8 URL 인코딩 파일명 (WSGI latin-1 헤더 인코딩 오류 방지)
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

            base_n, ext_n = os.path.splitext(safe_filename)
            dest_path = os.path.join(dest_dir, safe_filename)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(dest_dir, f"{base_n}_{counter}{ext_n}")
                counter += 1

            os.rename(temp_dest, dest_path)
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
                    g["rom_url"] = f"{ROUTE_BASE}/rom/{g['id']}/{urllib.parse.quote(g['filename'])}"
                    g["save_url"] = f"{ROUTE_BASE}/save/{g['id']}?user_id={user_id}"
                    g["state_url"] = f"{ROUTE_BASE}/state/{g['id']}?user_id={user_id}"
                    g["cover_url"] = f"{ROUTE_BASE}/cover/{g['id']}"

                available_bios = []
                game_filenames = {g["filename"].lower() for g in games}

                # 1. bios/ 폴더 내 모든 파일
                bios_dir = self._get_bios_dir()
                if os.path.exists(bios_dir):
                    try:
                        for root, _, files in os.walk(bios_dir):
                            for f in files:
                                if not f.startswith("."):
                                    available_bios.append(f.lower())
                    except Exception:
                        pass

                # 2. roms/ 및 추가 경로 내의 바이오스/MAME 디바이스 파일 (게임 목록에 없는 800+ 펌웨어 롬 포함)
                scan_dirs = [self._get_roms_dir()]
                extra_p = self._get_setting("EXTRA_ROMS_PATH", "").strip()
                if extra_p and os.path.isdir(extra_p):
                    scan_dirs.append(extra_p)
                for sdir in scan_dirs:
                    if os.path.exists(sdir):
                        try:
                            for root, _, files in os.walk(sdir):
                                for f in files:
                                    if not f.startswith("."):
                                        lower_f = f.lower()
                                        if lower_f not in game_filenames or _is_bios_file(f):
                                            available_bios.append(lower_f)
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
                    },
                }

            elif action == "scan_roms":
                self._scan_roms()
                return {"success": True, "message": "ROM 폴더 스캔이 완료되었습니다."}

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

                extra_path = request.args.get("extra_roms_path", "").strip()
                cloud_save_raw = request.args.get("cloud_save_enabled", "1")
                interval_raw = request.args.get("auto_save_interval_sec", "60")

                cloud_save = True if str(cloud_save_raw).lower() in ("1", "true", "yes", "on") else False
                try:
                    interval = int(interval_raw)
                except Exception:
                    interval = 60

                self._set_setting("EXTRA_ROMS_PATH", extra_path)
                self._set_setting("CLOUD_SAVE_ENABLED", cloud_save)
                self._set_setting("AUTO_SAVE_INTERVAL_SEC", interval)

                self._scan_roms()
                return {"success": True, "message": "설정이 성공적으로 저장되었습니다."}

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
