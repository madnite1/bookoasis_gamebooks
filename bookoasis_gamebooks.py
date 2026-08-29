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
from pathlib import Path, PurePosixPath

from plugins.metadata.base import BaseMetadataProvider

# 플러그인 전용 격리 패키지(libs/) sys.path 등록
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)
_LIBS_DIR = os.path.join(_PLUGIN_DIR, "libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)



logger = logging.getLogger(__name__)


def _path_within(path, root):
    """Resolve symlinks and verify that path is root itself or one of its descendants."""
    try:
        p = Path(path).expanduser().resolve(strict=False)
        r = Path(root).expanduser().resolve(strict=False)
        return p == r or r in p.parents
    except Exception:
        return False


def _path_within_any(path, roots):
    return any(root and _path_within(path, root) for root in roots)


def _safe_7z_extract(archive_path, dest_dir, max_files=4096, max_unpacked_bytes=None):
    """Extract a 7z archive only after path/count/size validation."""
    import py7zr

    max_unpacked_bytes = max_unpacked_bytes or int(os.environ.get("GAMEBOOKS_MAX_7Z_UNPACKED_MB", "2048")) * 1024 * 1024
    dest_root = Path(dest_dir).resolve(strict=False)
    with py7zr.SevenZipFile(str(archive_path), mode="r") as z7:
        names = z7.getnames()
        if len(names) > max_files:
            raise ValueError(f"7z file count exceeds limit ({len(names)} > {max_files})")
        for raw_name in names:
            normalized = str(raw_name).replace("\\", "/")
            pp = PurePosixPath(normalized)
            if pp.is_absolute() or ".." in pp.parts or (pp.parts and ":" in pp.parts[0]):
                raise ValueError(f"unsafe 7z member path: {raw_name}")
        try:
            total_declared = 0
            for info in z7.list():
                size = getattr(info, "uncompressed", None)
                if size is None:
                    size = getattr(info, "size", 0)
                total_declared += int(size or 0)
            if total_declared > max_unpacked_bytes:
                raise ValueError(f"7z unpacked size exceeds limit ({total_declared} bytes)")
        except ValueError:
            raise
        except Exception:
            pass
        z7.extract(path=str(dest_root))

    file_count = 0
    total_size = 0
    for root, _dirs, files in os.walk(dest_root):
        for name in files:
            fp = Path(root) / name
            if not _path_within(fp, dest_root):
                raise ValueError(f"7z extraction escaped destination: {fp}")
            file_count += 1
            total_size += fp.stat().st_size
            if file_count > max_files or total_size > max_unpacked_bytes:
                raise ValueError("7z extraction exceeded safety limits")
    return file_count, total_size


def _validate_zip_file(path):
    """Return True only for a readable, non-empty ZIP with no CRC errors."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        with zipfile.ZipFile(path, "r") as zf:
            if not zf.infolist():
                return False
            return zf.testzip() is None
    except Exception:
        return False


SELF_ID = "bookoasis_gamebooks"
ROUTE_BASE = f"/api/webhook/{SELF_ID}"

_DB_LOCK = threading.Lock()
_ROUTES_LOCK = threading.Lock()
_REGISTERED_APPS = set()

KST = timezone(timedelta(hours=9))

_SCAN_PROGRESS = {
    "is_running": False,
    "current": 0,
    "total": 0,
    "current_file": "",
    "status": "",
    "updated_at": 0,
}
_SCAN_PROGRESS_LOCK = threading.Lock()

# 백그라운드 커버 아트 다운로드 전역 큐 매니저
_COVER_QUEUE = []
_COVER_QUEUE_SET = set()  # 중복 등록 방지 (gid 기준)
_COVER_QUEUE_LOCK = threading.Lock()
_COVER_QUEUE_RUNNING = False
_COVER_QUEUE_STATS = {
    "is_running": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "current_title": "",
    "updated_at": 0,
}


def _enqueue_cover_downloads(provider_inst, items):
    """(gid, core_or_plat, fname, fpath, raw_title) 튜플 리스트를 다운로드 큐에 추가하고 워커 스레드를 가동"""
    global _COVER_QUEUE_RUNNING
    if not items:
        return
    with _COVER_QUEUE_LOCK:
        added_count = 0
        for it in items:
            gid = it[0]
            if gid not in _COVER_QUEUE_SET:
                _COVER_QUEUE_SET.add(gid)
                _COVER_QUEUE.append(it)
                added_count += 1
        _COVER_QUEUE_STATS["total"] += added_count
        _COVER_QUEUE_STATS["updated_at"] = time.time()

        if not _COVER_QUEUE_RUNNING and _COVER_QUEUE:
            _COVER_QUEUE_RUNNING = True
            _COVER_QUEUE_STATS["is_running"] = True
            threading.Thread(target=_cover_queue_worker, args=(provider_inst,), daemon=True).start()


def _cover_queue_worker(provider_inst):
    """큐에서 항목을 하나씩 꺼내어 커버 아트를 다운로드하는 백그라운드 워커 스레드"""
    global _COVER_QUEUE_RUNNING
    try:
        while True:
            item = None
            with _COVER_QUEUE_LOCK:
                if _COVER_QUEUE:
                    item = _COVER_QUEUE.pop(0)
                else:
                    _COVER_QUEUE_RUNNING = False
                    _COVER_QUEUE_STATS["is_running"] = False
                    _COVER_QUEUE_STATS["current_title"] = ""
                    _COVER_QUEUE_STATS["updated_at"] = time.time()
                    break

            if not item:
                break

            g_id, core_p, fname, fpath, raw_t = item
            with _COVER_QUEUE_LOCK:
                _COVER_QUEUE_STATS["current_title"] = raw_t or fname
                _COVER_QUEUE_STATS["updated_at"] = time.time()

            try:
                res = provider_inst._auto_fetch_and_save_cover(g_id, core_p, fname, file_path=fpath, raw_title=raw_t)
                with _COVER_QUEUE_LOCK:
                    if res:
                        _COVER_QUEUE_STATS["completed"] += 1
                    else:
                        _COVER_QUEUE_STATS["failed"] += 1
                    _COVER_QUEUE_STATS["updated_at"] = time.time()
            except Exception as e:
                logger.debug(f"[{SELF_ID}] Cover queue worker item error ({fname}): {e}")
                with _COVER_QUEUE_LOCK:
                    _COVER_QUEUE_STATS["failed"] += 1
                    _COVER_QUEUE_STATS["updated_at"] = time.time()
            
            # 외부 API 요청 간 미세 딜레이
            time.sleep(0.05)
    except Exception as ex:
        logger.error(f"[{SELF_ID}] Cover queue worker global error: {ex}")
    finally:
        with _COVER_QUEUE_LOCK:
            _COVER_QUEUE_RUNNING = False
            _COVER_QUEUE_STATS["is_running"] = False


def _get_cover_queue_status():
    """현재 커버 큐 상태 반환"""
    with _COVER_QUEUE_LOCK:
        remaining = len(_COVER_QUEUE)
        stats = dict(_COVER_QUEUE_STATS)
        stats["remaining"] = remaining
        return stats


def _update_scan_progress(current=None, total=None, current_file=None, status=None, is_running=None):
    with _SCAN_PROGRESS_LOCK:
        if current is not None:
            _SCAN_PROGRESS["current"] = current
        if total is not None:
            _SCAN_PROGRESS["total"] = total
        if current_file is not None:
            _SCAN_PROGRESS["current_file"] = current_file
        if status is not None:
            _SCAN_PROGRESS["status"] = status
        if is_running is not None:
            _SCAN_PROGRESS["is_running"] = is_running
        _SCAN_PROGRESS["updated_at"] = time.time()


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


def _calc_sha1(file_path):
    """파일 SHA1 계산"""
    try:
        h = hashlib.sha1()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest().lower()
    except Exception:
        return None


def _analyze_title_tokens(text):
    raw = str(text or "").strip()
    if not raw:
        return {
            "normalized_title": "",
            "region_tag": "",
            "revision_tag": "",
            "disc_number": 0,
            "content_flags": "",
        }

    region_tags = []
    revision_tags = []
    content_flags = []
    disc_number = 0

    tag_pattern = re.compile(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]")
    for match in tag_pattern.findall(raw):
        token = str(match).strip()
        lower = token.lower()
        if lower in ("usa", "us", "u", "japan", "jp", "j", "europe", "eu", "pal", "korea", "kr", "world"):
            region_tags.append(token)
        elif re.search(r"\brev(?:ision)?\b|v\d+(?:\.\d+)?|proto|beta", lower):
            revision_tags.append(token)
        elif re.search(r"\bdisc\s*([0-9]+)|\bcd\s*([0-9]+)", lower):
            m = re.search(r"([0-9]+)", lower)
            if m:
                disc_number = max(disc_number, int(m.group(1)))
        elif "translation" in lower or "trans" in lower:
            content_flags.append("translation")
        elif "hack" in lower or "patch" in lower:
            content_flags.append("hack")
        elif "homebrew" in lower or "demo" in lower:
            content_flags.append("homebrew")

    clean = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", raw)
    clean = clean.replace("_", " ").replace("-", " ")
    clean = re.sub(r"[^0-9A-Za-z가-힣:!'&+,./\s]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return {
        "normalized_title": clean.lower(),
        "region_tag": ", ".join(dict.fromkeys(region_tags)),
        "revision_tag": ", ".join(dict.fromkeys(revision_tags)),
        "disc_number": disc_number,
        "content_flags": ",".join(dict.fromkeys(content_flags)),
    }


def _basic_normalize_title(text):
    return _analyze_title_tokens(text).get("normalized_title", "")


def _collect_identity_fields(file_path, rom_info, clean_title="", size_bytes=0):
    ext = os.path.splitext(str(file_path or ""))[1].lower()
    serial_code = str(rom_info.get("serial_code") or rom_info.get("game_code") or "").strip()
    resolved_disk_files = [p for p in (rom_info.get("resolved_disk_files") or []) if p]
    if not serial_code:
        if ext in (".cue", ".gdi") and resolved_disk_files:
            for resolved_path in resolved_disk_files:
                serial_code = _scan_cd_serial(resolved_path) or ""
                if serial_code:
                    break
        elif ext in (".bin", ".iso", ".img", ".chd", ".pbp"):
            serial_code = _scan_cd_serial(file_path) or ""

    rom_crc32 = ""
    rom_md5 = ""
    rom_sha1 = ""
    size_bytes = int(size_bytes or 0)
    should_hash = size_bytes > 0 and size_bytes <= 64 * 1024 * 1024 and os.path.isfile(file_path)
    if should_hash:
        rom_crc32 = _calc_crc32(file_path) or ""
        rom_md5 = _calc_md5(file_path) or ""
        rom_sha1 = _calc_sha1(file_path) or ""

    title_analysis = _analyze_title_tokens(clean_title or rom_info.get("title") or os.path.basename(file_path))
    source_system = str(rom_info.get("source_system") or "filename").strip() or "filename"
    return {
        "rom_crc32": rom_crc32,
        "rom_md5": rom_md5,
        "rom_sha1": rom_sha1,
        "serial_code": serial_code,
        "normalized_title": title_analysis.get("normalized_title") or "",
        "region_tag": title_analysis.get("region_tag") or "",
        "revision_tag": title_analysis.get("revision_tag") or "",
        "disc_number": int(title_analysis.get("disc_number") or 0),
        "content_flags": title_analysis.get("content_flags") or "",
        "source_system": source_system,
        "metadata_source": str(rom_info.get("metadata_source") or "").strip(),
        "metadata_confidence": int(rom_info.get("metadata_confidence") or 0),
    }


def _pick_preferred_dict_text(value, preferred_keys=None):
    preferred_keys = preferred_keys or ("korean", "kr", "ko", "jp", "ja", "us", "en", "wor", "ss")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}
        for key in preferred_keys:
            if key in lowered:
                picked = _pick_preferred_dict_text(lowered.get(key), preferred_keys)
                if picked:
                    return picked
        for sub in lowered.values():
            picked = _pick_preferred_dict_text(sub, preferred_keys)
            if picked:
                return picked
    if isinstance(value, list):
        for item in value:
            picked = _pick_preferred_dict_text(item, preferred_keys)
            if picked:
                return picked
    return ""


def _ss_extract_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _pick_preferred_dict_text(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _ss_extract_text(item)
            if text:
                parts.append(text)
        return ", ".join(dict.fromkeys(parts))
    return ""


def _fetch_screenscraper_gameinfo(file_path, platform_or_core, filename, sc_config):
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
        req_url = f"https://www.screenscraper.fr/api2/jeuInfos.php?{query_str}"
        req = urllib.request.Request(req_url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", {}).get("jeu") or None
    except Exception as e:
        logger.debug(f"[{SELF_ID}] ScreenScraper query error: {e}")
    return None


def _extract_screenscraper_artwork(game_payload):
    medias = []
    if isinstance(game_payload, dict):
        medias = game_payload.get("medias", []) or []
    for media in medias:
        if not isinstance(media, dict):
            continue
        mtype = str(media.get("type") or "").lower()
        if mtype in ("box-2d", "box-3d", "wheel", "screenshot"):
            img_url = media.get("url")
            if img_url:
                try:
                    img_req = urllib.request.Request(img_url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
                    with urllib.request.urlopen(img_req, timeout=15) as img_resp:
                        if img_resp.status == 200:
                            img_data = img_resp.read()
                            if img_data and len(img_data) > 512:
                                return img_data
                except Exception as e:
                    logger.debug(f"[{SELF_ID}] ScreenScraper artwork fetch error: {e}")
    return None


def _extract_screenscraper_metadata(game_payload):
    if not isinstance(game_payload, dict):
        return {}
    titles = game_payload.get("noms") or game_payload.get("names") or game_payload.get("nom") or ""
    canonical_title = _ss_extract_text(titles)
    synopsis = game_payload.get("synopsis") or game_payload.get("synopsys") or game_payload.get("description") or ""
    genre = _ss_extract_text(game_payload.get("genres") or game_payload.get("genre") or "")
    developer = _ss_extract_text(game_payload.get("developpeur") or game_payload.get("developer") or "")
    publisher = _ss_extract_text(game_payload.get("editeur") or game_payload.get("publisher") or "")
    region = _ss_extract_text(game_payload.get("regions") or game_payload.get("region") or "")
    players_raw = _ss_extract_text(game_payload.get("joueurs") or game_payload.get("players") or "")
    release_year = _ss_extract_text(game_payload.get("dates") or game_payload.get("date") or game_payload.get("annee") or "")
    if release_year:
        year_match = re.search(r"(19|20)\d{2}", release_year)
        release_year = year_match.group(0) if year_match else release_year[:10]
    players = 0
    if players_raw:
        players_match = re.search(r"\d+", players_raw)
        if players_match:
            players = int(players_match.group(0))
    alt_titles = []
    if isinstance(titles, dict):
        for value in titles.values():
            text = _ss_extract_text(value)
            if text and text != canonical_title:
                alt_titles.append(text)
    alt_titles = list(dict.fromkeys(alt_titles))
    return {
        "canonical_title": canonical_title,
        "region": region,
        "genre": genre,
        "developer": developer,
        "publisher": publisher,
        "release_year": release_year,
        "players": players,
        "description": _ss_extract_text(synopsis),
        "alt_titles": json.dumps(alt_titles, ensure_ascii=False) if alt_titles else "",
        "metadata_source": "screenscraper",
        "metadata_confidence": 90 if canonical_title else 70,
    }


def _fetch_screenscraper_metadata(file_path, platform_or_core, filename, sc_config):
    game_payload = _fetch_screenscraper_gameinfo(file_path, platform_or_core, filename, sc_config)
    if not game_payload:
        return {}
    return _extract_screenscraper_metadata(game_payload)


def _fetch_screenscraper_artwork(file_path, platform_or_core, filename, sc_config):
    """ScreenScraper API를 질의하여 롬 아트워크 다운로드 (Key 설정 시에만 동작)"""
    game_payload = _fetch_screenscraper_gameinfo(file_path, platform_or_core, filename, sc_config)
    if not game_payload:
        return None
    return _extract_screenscraper_artwork(game_payload)


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


def _build_igdb_search_title(raw_title):
    clean_t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", str(raw_title or "")).strip()
    clean_t = clean_t.replace("_", " ").replace("-", " ")
    clean_t = re.sub(r"\b(rev|proto|beta|translation|hack|disc|disk)\b.*$", "", clean_t, flags=re.IGNORECASE).strip()
    clean_t = re.sub(r"\s+", " ", clean_t).strip()
    return clean_t or str(raw_title or "").strip()


def _igdb_platform_tokens(platform_or_core):
    key = str(platform_or_core or "").lower().strip()
    mapping = {
        "gba": {"game boy advance", "gba"},
        "gb": {"game boy", "gb"},
        "gbc": {"game boy color", "gbc"},
        "snes": {"super nintendo", "snes", "super famicom"},
        "nes": {"nes", "famicom"},
        "n64": {"n64", "nintendo 64"},
        "psx": {"ps1", "psx", "playstation"},
        "ps1": {"ps1", "playstation"},
        "psp": {"psp", "playstation portable"},
        "nds": {"nds", "nintendo ds"},
        "segamd": {"genesis", "mega drive"},
        "genesis": {"genesis", "mega drive"},
        "arcade": {"arcade"},
        "mame2003": {"arcade"},
        "neogeo": {"neo geo", "arcade"},
        "neo-geo": {"neo geo", "arcade"},
        "pce": {"pc engine", "turbografx"},
        "saturn": {"saturn", "sega saturn"},
    }
    return mapping.get(key, {key} if key else set())


def _score_igdb_candidate(candidate, platform_or_core):
    score = 0
    platforms = candidate.get("platforms") or []
    tokens = _igdb_platform_tokens(platform_or_core)
    if tokens:
        for plat in platforms:
            name = str(plat.get("name") or "").lower()
            abbr = str(plat.get("abbreviation") or "").lower()
            if any(token in name or token == abbr for token in tokens):
                score += 20
                break
    if candidate.get("summary"):
        score += 5
    if candidate.get("genres"):
        score += 5
    return score


def _fetch_igdb_game(raw_title, platform_or_core, igdb_config):
    client_id = igdb_config.get("igdb_client_id")
    client_secret = igdb_config.get("igdb_client_secret")
    if not client_id or not client_secret or not raw_title:
        return None
    token = _get_igdb_token(client_id, client_secret)
    if not token:
        return None

    clean_t = _build_igdb_search_title(raw_title)
    escaped_title = clean_t.replace('"', '\\"')
    query_body = (
        f'search "{escaped_title}"; '
        'fields name,summary,genres.name,first_release_date,involved_companies.company.name,franchises.name,cover.image_id,cover.url,platforms.name,platforms.abbreviation; '
        'limit 5;'
    )
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
                    ranked = sorted(results, key=lambda item: _score_igdb_candidate(item, platform_or_core), reverse=True)
                    return ranked[0]
    except Exception as e:
        logger.debug(f"[{SELF_ID}] IGDB query error: {e}")
    return None


def _fetch_igdb_metadata(raw_title, platform_or_core, igdb_config):
    game = _fetch_igdb_game(raw_title, platform_or_core, igdb_config)
    if not game:
        return {}
    genres = ", ".join(g.get("name", "") for g in (game.get("genres") or []) if g.get("name"))
    companies = []
    for item in game.get("involved_companies") or []:
        comp = item.get("company") or {}
        name = comp.get("name")
        if name:
            companies.append(name)
    franchise = ""
    franchises = game.get("franchises") or []
    if franchises:
        franchise = str(franchises[0].get("name") or "").strip()
    release_year = ""
    ts = game.get("first_release_date")
    if ts:
        try:
            release_year = datetime.utcfromtimestamp(int(ts)).strftime("%Y")
        except Exception:
            release_year = ""
    return {
        "canonical_title": str(game.get("name") or "").strip(),
        "genre": genres,
        "developer": ", ".join(dict.fromkeys(companies)),
        "publisher": ", ".join(dict.fromkeys(companies[:1])),
        "description": str(game.get("summary") or "").strip(),
        "release_year": release_year,
        "franchise": franchise,
        "metadata_source": "igdb",
        "metadata_confidence": 55,
    }


def _fetch_igdb_artwork(raw_title, igdb_config):
    """IGDB API를 질의하여 고화질 커버 아트 다운로드 (Key 설정 시에만 동작)"""
    game = _fetch_igdb_game(raw_title, "", igdb_config)
    if not game:
        return None
    cover = game.get("cover")
    if not cover:
        return None
    img_id = cover.get("image_id")
    if img_id:
        img_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{img_id}.jpg"
    else:
        raw_url = cover.get("url", "")
        img_url = ("https:" + raw_url) if raw_url.startswith("//") else raw_url
    if not img_url:
        return None
    try:
        img_req = urllib.request.Request(img_url, headers={"User-Agent": "BookOasis-GameBooks/1.2"})
        with urllib.request.urlopen(img_req, timeout=12) as img_resp:
            if img_resp.status == 200:
                img_data = img_resp.read()
                if img_data and len(img_data) > 512:
                    return img_data
    except Exception as e:
        logger.debug(f"[{SELF_ID}] IGDB artwork error: {e}")
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

        # 인증된 세션만 사용자 식별에 사용합니다.
        # 쿼리스트링 user_id를 신뢰하면 다른 사용자의 세이브/즐겨찾기를 가장할 수 있습니다.
    except Exception:
        pass
    return 0


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

DISK_IMAGE_EXTS = {".bin", ".iso", ".img", ".chd", ".pbp", ".cue", ".gdi"}

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
    "bldyror2": "블러디 로어 2 (동물철권 2)",
    "bldyror": "블러디 로어 (동물철권)",
    "brvblade": "브레이브 블레이드 (Brave Blade)",
    "sfex": "스트리트 파이터 EX",
    "sfexplus": "스트리트 파이터 EX 플러스",
    "sfexp": "스트리트 파이터 EX 플러스",
    "sfex2": "스트리트 파이터 EX 2",
    "sfex2p": "스트리트 파이터 EX 2 플러스",
    "rvschool": "사립 저스티스 학원 (Rival Schools)",
    "jgts": "사립 저스티스 학원 (일판)",
    "starglad": "스타 글래디에이터 (Star Gladiator)",
    "strider2": "스트라이더 비룡 2 (Strider 2)",
    "techromn": "초강전기 키카이오 (Tech Romancer)",
    "raiden2": "라이덴 2 (Raiden II)",
    "raidendx": "라이덴 DX (Raiden DX)",
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
    "ddp2": "돈파치 2: 비 스톰 (DoDonPachi II)",
    "kov2": "삼국전기 2 (Knights of Valour 2)",
    "kov2p": "삼국전기 2 플러스 (Knights of Valour 2 Plus)",
    "svg": "스펙트럴 vs 제너레이션 (Spectral vs Generation)",
    "theglad": "검마전설 (The Gladiator / 신검의 패)",
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

    # 슈퍼로봇대전 / 드래곤볼 / 젤다 / 파이어 엠블렘 / 로맨싱 사가 / 풍래의 시렌 등
    "dai 4 ji super robot taisen kai 2 ex": "제4차 슈퍼로봇대전 개2 EX",
    "dai 4 ji super robot taisen kai 2": "제4차 슈퍼로봇대전 개2",
    "dai 4 ji super robot taisen kai": "제4차 슈퍼로봇대전 개",
    "dai 4 ji super robot taisen": "제4차 슈퍼로봇대전",
    "dai 3 ji super robot taisen": "제3차 슈퍼로봇대전",
    "super robot taisen": "슈퍼로봇대전",
    "super robot wars": "슈퍼로봇대전",
    "battle robot retsuden": "배틀 로봇 열전",
    "densetsu no orge battle": "전설의 오우거 배틀",
    "fire emblem genearaly of holy war": "파이어 엠블렘: 성전의 계보",
    "genealogy of the holy war": "파이어 엠블렘: 성전의 계보",
    "fire emblem": "파이어 엠블렘",
    "furai no siren": "풍래의 시렌",
    "fuurai no shiren": "풍래의 시렌",
    "korean pro base ball game": "한국 프로 야구 (Korean Pro Baseball)",
    "romancing saga 3": "로맨싱 사가 3",
    "romancing saga 2": "로맨싱 사가 2",
    "romancing saga": "로맨싱 사가",
    "slyers": "슬레이어즈 (Slayers)",
    "slayers": "슬레이어즈 (Slayers)",
    "starwars 5": "스타워즈 에피소드 5: 제국의 역습",
    "starwars 6": "스타워즈 에피소드 6: 제다이의 귀환",
    "star wars": "스타워즈",
    "choujikuu yousai macross": "초시공요새 마크로스: 스크램블드 발키리",
    "macross scrambled valkyrie": "초시공요새 마크로스: 스크램블드 발키리",
    "macross": "마크로스 (Macross)",
    "clock tower": "클락 타워 (Clock Tower)",
    "dragon ball z hyper dimension": "드래곤볼 Z: 하이퍼 디멘션",
    "dragon ball z super butouden 2": "드래곤볼 Z: 초무투전 2",
    "dragon ball z super butouden": "드래곤볼 Z: 초무투전",
    "dragon ball z": "드래곤볼 Z",
    "dragonball z": "드래곤볼 Z",
    "dragon ball": "드래곤볼",
    "dbzchom": "드래곤볼 Z: 초무투전",
    "majuuou": "마수왕 (Majuuou)",
    "sailorss": "미소녀전사 세일러문 SuperS",
    "tmnt4a": "닌자 거북이 4: 터틀스 인 타임 (TMNT IV)",
    "chomakai": "초마계촌 (Super Ghouls 'n Ghosts)",
    "genocid2": "제노사이드 2",
    "pocky2u": "기기괴괴: 월야초자 (Pocky & Rocky 2)",
    "popntwinj": "트윈비 레인보우 벨 어드벤처",
    "sbm2": "슈퍼 봄버맨 2",
    "sdgungx": "SD 건담 GX",
    "sfz2": "스트리트 파이터 제로 2",
    "smkartu": "슈퍼 마리오 카트",
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

    return clean or name or os.path.splitext(os.path.basename(filename))[0]

def _is_valid_header_title(text):
    """내부 바이너리 헤더에서 추출된 텍스트가 유효한 영문/숫자 게임명인지 검증 (깨진 바이너리/특수문자 찌꺼기 폐기)"""
    if not text or not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 3 or t.startswith("???"):
        return False
    # 깨진 문자 패턴 (@, %, \, /, <, >, ^, $, [, ], {, } 등이 2개 이상 포함되거나 영숫자 비율이 낮은 경우)
    bad_chars = sum(1 for c in t if c in "@%\\/<>^$[]{}~`|=#*")
    if bad_chars >= 2:
        return False
    valid_chars = sum(1 for c in t if c.isalnum() or c in " -_':.!,&()")
    if len(t) > 0 and (valid_chars / len(t)) < 0.8:
        return False
    # 연속된 동일 특수문자 (예: @@@@@)
    if re.search(r"([^a-zA-Z0-9\s])\1{2,}", t):
        return False
    return True


def _scan_cd_serial(file_path):
    """CD/디스크 이미지(.bin, .iso, .img, .chd) 앞부분 2MB를 초고속 스캔하여 PS1/Saturn 고유 시리얼 번호 추출"""
    serial_pattern = re.compile(rb"([CS][LUE][PUE][SAM][_\-][0-9]{3}[_\.][0-9]{2})|([CS][LUE][PUE][SAM][0-9]{5})")
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(2 * 1024 * 1024)
            match = serial_pattern.search(chunk)
            if match:
                raw_serial = (match.group(1) or match.group(2)).decode("ascii", errors="ignore")
                clean_serial = re.sub(r"[^A-Za-z0-9]", "-", raw_serial).upper()
                return clean_serial
    except Exception:
        pass
    return None


def _parse_cue_tracks(file_path):
    tracks = []
    current_file = ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                file_match = re.match(r'^FILE\s+"([^"]+)"\s+.+$', line, re.IGNORECASE)
                if file_match:
                    current_file = file_match.group(1).strip()
                    continue
                track_match = re.match(r'^TRACK\s+(\d+)\s+(.+)$', line, re.IGNORECASE)
                if track_match:
                    tracks.append({
                        "track": int(track_match.group(1)),
                        "mode": track_match.group(2).strip(),
                        "file": current_file,
                    })
    except Exception:
        return []
    return tracks


def _iter_disk_related_dirs(file_path):
    base_dir = os.path.dirname(os.path.abspath(str(file_path or "")))
    if not base_dir:
        return []

    parent_dir = os.path.dirname(base_dir)
    related_dirs = [base_dir]
    sibling_dirs = []

    if parent_dir and os.path.isdir(parent_dir):
        try:
            for entry in sorted(os.listdir(parent_dir)):
                if entry.startswith("."):
                    continue
                cand_dir = os.path.join(parent_dir, entry)
                if os.path.isdir(cand_dir):
                    sibling_dirs.append(cand_dir)
        except Exception:
            sibling_dirs = []

    isos_dir = os.path.join(parent_dir, "isos") if parent_dir else ""
    if isos_dir and os.path.isdir(isos_dir):
        related_dirs.append(isos_dir)

    for cand_dir in sibling_dirs:
        normalized = os.path.abspath(cand_dir)
        if normalized == base_dir:
            continue
        if isos_dir and normalized == os.path.abspath(isos_dir):
            continue
        related_dirs.append(normalized)

    unique = []
    seen = set()
    for cand_dir in related_dirs:
        normalized = os.path.abspath(str(cand_dir or ""))
        if not normalized or normalized in seen or not os.path.isdir(normalized):
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _resolve_disk_track_path(file_path, rel_name):
    rel_name = str(rel_name or "").strip().strip('"')
    if not file_path or not rel_name:
        return ""
    if os.path.isabs(rel_name):
        return rel_name if os.path.exists(rel_name) else ""

    rel_path = os.path.normpath(rel_name.replace("\\", os.sep).replace("/", os.sep))
    rel_base = os.path.basename(rel_path)
    base_dir = os.path.dirname(os.path.abspath(file_path))
    parent_dir = os.path.dirname(base_dir)
    candidates = [os.path.join(base_dir, rel_path)]

    if parent_dir:
        candidates.append(os.path.join(parent_dir, rel_path))

    for search_dir in _iter_disk_related_dirs(file_path):
        if rel_base:
            candidates.append(os.path.join(search_dir, rel_base))
        if rel_path and rel_path != rel_base:
            candidates.append(os.path.join(search_dir, rel_path))

    seen = set()
    for candidate in candidates:
        normalized = os.path.abspath(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.exists(normalized):
            return normalized

    # Linux 대소문자 불일치 fallback (예: .cue 내부는 .BIN인데 실제 파일은 .bin인 경우)
    for search_dir in [base_dir] + (([parent_dir] if parent_dir else [])) + _iter_disk_related_dirs(file_path):
        if not search_dir or not os.path.isdir(search_dir):
            continue
        try:
            target_lower = rel_base.lower()
            for entry_name in os.listdir(search_dir):
                if entry_name.lower() == target_lower:
                    matched = os.path.join(search_dir, entry_name)
                    if os.path.exists(matched):
                        return os.path.abspath(matched)
        except Exception:
            continue

    return ""


def _resolve_disk_sidecars(file_path):
    ext = os.path.splitext(str(file_path or ""))[1].lower()
    missing = []
    details = {"missing_files": [], "resolved_files": [], "serial_code": "", "disc_count": 1}
    if ext == ".cue":
        tracks = _parse_cue_tracks(file_path)
        resolved_files = []
        details["disc_count"] = max(1, len(tracks))
        for track in tracks:
            rel_name = str(track.get("file") or "").strip()
            if not rel_name:
                continue
            resolved_path = _resolve_disk_track_path(file_path, rel_name)
            if resolved_path:
                resolved_files.append(resolved_path)
            else:
                missing.append(rel_name)
        details["resolved_files"] = resolved_files
        for resolved_path in resolved_files:
            serial = _scan_cd_serial(resolved_path)
            if serial:
                details["serial_code"] = serial
                break
        details["missing_files"] = missing
        return details
    if ext == ".gdi":
        resolved_files = []
        details["disc_count"] = 0
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [line.strip() for line in f if line.strip()]
            if lines:
                try:
                    details["disc_count"] = int(lines[0])
                except Exception:
                    details["disc_count"] = max(1, len(lines) - 1)
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 5:
                    rel_name = parts[4].strip('"')
                    resolved_path = _resolve_disk_track_path(file_path, rel_name)
                    if resolved_path:
                        resolved_files.append(resolved_path)
                    else:
                        missing.append(rel_name)
        except Exception:
            pass
        details["disc_count"] = max(1, details["disc_count"])
        details["resolved_files"] = resolved_files
        for resolved_path in resolved_files:
            serial = _scan_cd_serial(resolved_path)
            if serial:
                details["serial_code"] = serial
                break
        details["missing_files"] = missing
        return details
    if ext in (".bin", ".img"):
        cue_candidate = os.path.splitext(file_path)[0] + ".cue"
        if os.path.exists(cue_candidate):
            cue_details = _resolve_disk_sidecars(cue_candidate)
            cue_details["serial_code"] = cue_details.get("serial_code") or _scan_cd_serial(file_path) or ""
            return cue_details
    details["serial_code"] = _scan_cd_serial(file_path) or ""
    return details


def _disk_bundle_partner_paths(file_path):
    partners = []
    abs_path = os.path.abspath(str(file_path or ""))
    stem = os.path.splitext(abs_path)[0]
    parent_dir = os.path.dirname(os.path.dirname(abs_path))
    candidate_stems = [stem]

    base_name = os.path.basename(stem)
    if parent_dir and base_name:
        for search_dir in _iter_disk_related_dirs(file_path):
            candidate_stems.append(os.path.join(search_dir, base_name))

    seen = set()
    for candidate_stem in candidate_stems:
        normalized_stem = os.path.abspath(str(candidate_stem or ""))
        if not normalized_stem or normalized_stem in seen:
            continue
        seen.add(normalized_stem)
        for ext in (".cue", ".gdi", ".bin", ".img", ".ccd", ".sub", ".mds", ".iso"):
            candidate = normalized_stem + ext
            if candidate != abs_path and os.path.exists(candidate):
                partners.append(candidate)
    return partners


def _find_disk_manifest_for_sidecar(file_path):
    abs_path = os.path.abspath(str(file_path or ""))
    stem = os.path.splitext(abs_path)[0]
    parent_dir = os.path.dirname(os.path.dirname(abs_path))
    candidate_stems = [stem]
    base_name = os.path.basename(stem)

    if parent_dir and base_name:
        for search_dir in _iter_disk_related_dirs(file_path):
            candidate_stems.append(os.path.join(search_dir, base_name))

    seen = set()
    for candidate_stem in candidate_stems:
        normalized_stem = os.path.abspath(str(candidate_stem or ""))
        if not normalized_stem or normalized_stem in seen:
            continue
        seen.add(normalized_stem)
        for ext in (".cue", ".gdi"):
            candidate = normalized_stem + ext
            if os.path.exists(candidate):
                return candidate
    return ""


def _collect_disk_bundle_paths(file_path):
    file_path = os.path.abspath(str(file_path or ""))
    if not file_path or not os.path.exists(file_path):
        return []

    bundle = []
    seen = set()

    def _add(path):
        normalized = os.path.abspath(str(path or ""))
        if normalized and os.path.exists(normalized) and normalized not in seen:
            seen.add(normalized)
            bundle.append(normalized)

    ext = os.path.splitext(file_path)[1].lower()
    _add(file_path)

    if ext in (".cue", ".gdi"):
        details = _resolve_disk_sidecars(file_path)
        for resolved_path in details.get("resolved_files") or []:
            _add(resolved_path)
        for existing_path in list(bundle):
            for partner_path in _disk_bundle_partner_paths(existing_path):
                _add(partner_path)
        return bundle

    if ext in (".bin", ".img", ".ccd", ".sub", ".mds", ".iso"):
        for partner_path in _disk_bundle_partner_paths(file_path):
            _add(partner_path)
        manifest_path = _find_disk_manifest_for_sidecar(file_path)
        if manifest_path:
            for related_path in _collect_disk_bundle_paths(manifest_path):
                _add(related_path)
        return bundle

    return bundle


def _build_disk_file_url_map(game_id, file_path, primary_filename=None):
    file_path = os.path.abspath(str(file_path or ""))
    if not game_id or not file_path or not os.path.exists(file_path):
        return {}

    primary_name = os.path.basename(str(primary_filename or file_path))
    disk_urls = {}
    for bundle_path in _collect_disk_bundle_paths(file_path):
        bundle_name = os.path.basename(bundle_path)
        if not bundle_name or bundle_name == primary_name:
            continue
        disk_urls[bundle_name] = f"{ROUTE_BASE}/rom/{game_id}/{urllib.parse.quote(bundle_name)}"
    return disk_urls


def _generate_m3u_content_for_paths(file_paths):
    """멀티파일 ROM용 .m3u 플레이리스트 내용 생성 (cue가 있으면 cue만, 없으면 전체 목록)"""
    if not file_paths:
        return b""
    cue_names = [os.path.basename(p) for p in file_paths if os.path.splitext(p)[1].lower() == ".cue"]
    if cue_names:
        m3u_entries = sorted(cue_names)
    else:
        m3u_entries = sorted([os.path.basename(p) for p in file_paths if os.path.isfile(p)])
    return ("\n".join(m3u_entries) + "\n").encode("utf-8")


def _rewrite_disk_manifest_to_local_paths(file_path):
    file_path = os.path.abspath(str(file_path or ""))
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".cue", ".gdi") or not os.path.exists(file_path):
        return False

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return False

    changed = False
    rewritten = []
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        newline = "\n" if raw_line.endswith("\n") else ""

        if ext == ".cue":
            match = re.match(r'^(\s*FILE\s+")([^"]+)("\s+.+)$', line, re.IGNORECASE)
            if match:
                rel_name = os.path.basename(match.group(2).strip())
                new_line = f'{match.group(1)}{rel_name}{match.group(3)}'
                changed = changed or (new_line != line)
                rewritten.append(new_line + newline)
                continue
        elif ext == ".gdi":
            match = re.match(r'^(\s*\d+\s+\d+\s+\d+\s+\d+\s+)("[^"]+"|\S+)(\s+\d+\s*)$', line)
            if match:
                token = match.group(2).strip()
                quoted = token.startswith('"') and token.endswith('"')
                rel_name = os.path.basename(token.strip('"'))
                new_token = f'"{rel_name}"' if quoted else rel_name
                new_line = f'{match.group(1)}{new_token}{match.group(3)}'
                changed = changed or (new_line != line)
                rewritten.append(new_line + newline)
                continue

        rewritten.append(raw_line)

    if not changed:
        return False

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(rewritten)
        return True
    except Exception:
        return False


def _move_disk_bundle(file_path, target_dir, related_infos=None):
    file_path = os.path.abspath(str(file_path or ""))
    target_dir = os.path.abspath(str(target_dir or ""))
    bundle_paths = _collect_disk_bundle_paths(file_path)
    if not file_path or not target_dir or not bundle_paths:
        return {"moved": False, "primary_path": file_path, "move_map": {}, "conflict": ""}

    move_map = {}
    for src_path in bundle_paths:
        dest_path = os.path.join(target_dir, os.path.basename(src_path))
        if os.path.abspath(dest_path) == src_path:
            continue
        if os.path.exists(dest_path):
            return {"moved": False, "primary_path": file_path, "move_map": {}, "conflict": dest_path}
        move_map[src_path] = dest_path

    os.makedirs(target_dir, exist_ok=True)
    manifest_exts = {".cue", ".gdi"}
    move_order = [p for p in bundle_paths if os.path.splitext(p)[1].lower() not in manifest_exts]
    move_order += [p for p in bundle_paths if os.path.splitext(p)[1].lower() in manifest_exts]

    for src_path in move_order:
        dest_path = move_map.get(src_path)
        if not dest_path:
            continue
        shutil.move(src_path, dest_path)

    for path in bundle_paths:
        final_path = move_map.get(path, path)
        if os.path.splitext(final_path)[1].lower() in manifest_exts:
            _rewrite_disk_manifest_to_local_paths(final_path)

    if related_infos:
        for info in related_infos:
            old_path = os.path.abspath(str(info.get("file_path") or ""))
            if old_path in move_map:
                new_path = move_map[old_path]
                info["file_path"] = new_path
                info["filename"] = os.path.basename(new_path)
                try:
                    info["size_bytes"] = os.path.getsize(new_path)
                    info["mtime"] = os.path.getmtime(new_path)
                except Exception:
                    pass

    return {
        "moved": bool(move_map),
        "primary_path": move_map.get(file_path, file_path),
        "move_map": move_map,
        "conflict": "",
    }


def _is_bios_file(file_or_path):
    """주요 에뮬레이터 및 아케이드(MAME/FBNeo) 기판 바이오스/디바이스 파일 여부 동적 및 정적 분석"""
    fname = os.path.basename(file_or_path).lower()
    stem = os.path.splitext(fname)[0]

    # 1. 정적 사전 및 명시적 키워드 매칭
    if stem in KNOWN_BIOS_STEMS or "bios" in stem or stem in ("boardrom", "bootrom", "sysrom", "firmware"):
        return True
    if fname.startswith("bios_") or fname.startswith("scph") or fname in ("disksys.rom", "syscard3.pce"):
        return True

    # 2. 실제 파일 경로가 존재하는 경우 ZIP 내부 구조 동적 심층 분석
    if os.path.isfile(file_or_path) and zipfile.is_zipfile(file_or_path):
        try:
            with zipfile.ZipFile(file_or_path, "r") as z:
                valid_entries = [info for info in z.infolist() if not info.filename.startswith((".", "__MACOSX")) and not info.is_dir()]
                if not valid_entries:
                    return False

                names = [e.filename.lower() for e in valid_entries]

                # (1) ZIP 내부 파일명에 바이오스/펌웨어/기판 칩셋 시그니처만으로 구성된 순수 바이오스 아카이브 검사
                total_size = sum(e.file_size for e in valid_entries)
                if total_size <= 2 * 1024 * 1024:
                    if any(any(k in n for k in ("bios", "boardrom", "bootrom", "firmware", "coh-1000", "coh-1001")) for n in names):
                        return True

                # (2) 단일/소수 칩 덤프이면서 콘솔 표준 헤더가 없고 크기가 극소형(64KB 이하) 단일 펌웨어인 경우
                if len(valid_entries) <= 2 and total_size <= 65536:
                    # 콘솔 표준 확장자가 없는 단일 펌웨어 파일인 경우 기판 바이오스로 판별
                    has_console_ext = any(os.path.splitext(n)[1] in SUPPORTED_SYSTEMS for n in names)
                    if not has_console_ext:
                        return True
        except Exception:
            pass

    return False


def _normalize_required_archive(name):
    req = str(name or "").strip().lower()
    if not req:
        return ""
    if not req.endswith((".zip", ".bin", ".rom", ".pce")):
        req += ".zip"
    return req


def _looks_like_bios_requirement(name):
    req = _normalize_required_archive(name)
    if not req:
        return False
    stem = os.path.splitext(req)[0]
    return _is_bios_file(req) or stem in KNOWN_BIOS_STEMS or stem in ("boardrom", "bootrom", "sysrom", "firmware")


def _is_optional_runtime_bios(name):
    req = _normalize_required_archive(name)
    return req.startswith("scph")


def _guess_arcade_required_chd(game_name, description=""):
    stem = re.sub(r"[^a-z0-9_\-]", "", str(game_name or "").strip().lower())
    desc = str(description or "").lower()
    if not stem:
        return ""
    chd_prefixes = ("bm", "ddr", "popn", "gfdm", "jubeat")
    chd_keywords = ("beatmania", "dance dance revolution", "guitar freaks", "drummania", "jubeat", "pop'n music")
    if stem.startswith(chd_prefixes) or any(k in desc for k in chd_keywords):
        return f"{stem}.chd"
    return ""


def _build_arcade_dat_result(gname, desc, romof, cloneof, sys_name, plat, matched_count, total_roms):
    total_roms = int(total_roms or 0)
    matched_count = int(matched_count or 0)
    match_rate = round((matched_count / total_roms * 100), 1) if total_roms > 0 else 100.0
    romof_name = _normalize_required_archive(romof)
    cloneof_name = _normalize_required_archive(cloneof)
    required_parent = cloneof_name or ""
    if not required_parent and romof_name and romof_name != f"{str(gname or '').strip().lower()}.zip" and not _looks_like_bios_requirement(romof_name):
        required_parent = romof_name
    required_bios = romof_name if _looks_like_bios_requirement(romof_name) else ""
    return {
        "name": gname,
        "description": desc,
        "romof": romof,
        "cloneof": cloneof,
        "system_name": sys_name,
        "platform": plat,
        "matched_count": matched_count,
        "total_roms": total_roms,
        "match_rate": match_rate,
        "is_non_merged": (match_rate >= 75.0),
        "required_parent": required_parent,
        "clone_of": cloneof_name,
        "required_bios": required_bios,
        "required_chd": _guess_arcade_required_chd(gname, desc),
        "missing_required_roms": [],
    }


def _has_valid_zip_payload(file_path, min_entries=1, min_total_bytes=1024):
    if not file_path or not os.path.exists(file_path):
        return False
    if not zipfile.is_zipfile(file_path):
        return False
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            entries = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith((".", "__MACOSX"))]
            if len(entries) < min_entries:
                return False
            return sum(max(0, i.file_size) for i in entries) >= min_total_bytes
    except Exception:
        return False


def _is_required_bios_available(required_bios, available_bios_names, bios_dir):
    req = _normalize_required_archive(required_bios)
    if not req or _is_optional_runtime_bios(req):
        return True
    available = {str(x or "").lower() for x in (available_bios_names or [])}
    if req not in available:
        return False
    if req not in {"neogeo.zip", "pgm.zip", "acpsx.zip"}:
        return True
    return _has_valid_zip_payload(os.path.join(bios_dir, req), min_entries=1, min_total_bytes=1024)

def _query_arcade_dat(stem, internal_crcs=None):
    """내장된 All-In-One DAT DB (Arcade + SNES + GBA + NES + MD + PCE 등) 조회"""
    dat_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arcade_dat.db")
    if not os.path.isfile(dat_db_path):
        return None

    clean_stem = re.sub(r"^\.+(temp_upload_)?", "", str(stem or "")).strip().lower()

    try:
        conn = sqlite3.connect(dat_db_path, timeout=5)
        cursor = conn.cursor()

        # 1. 내부 CRC32 완전 일치 조회 (FBNeo / MAME 및 콘솔 Softlist에서 100% 완전 일치하는 롬셋 검색)
        if internal_crcs:
            placeholders = ",".join(["?"] * len(internal_crcs))
            query_exact = f"""
                SELECT g.id, g.name, g.description, g.romof, g.cloneof, g.system_name, g.platform, COUNT(r.id) as matched,
                       (SELECT COUNT(*) FROM roms WHERE game_id = g.id) as total_cnt
                FROM roms r
                JOIN games g ON r.game_id = g.id
                WHERE r.crc32 IN ({placeholders}) AND g.name NOT IN ('pgm', 'neogeo', 'playch10', 'decocass', 'skns', 'stvbios', 'naomi', 'cpzn1', 'cpzn2')
                GROUP BY g.id
                HAVING matched = total_cnt
                ORDER BY (g.name = '{clean_stem}') DESC, matched DESC, (g.system_name = 'FBNeo') DESC
                LIMIT 1
            """
            cursor.execute(query_exact, internal_crcs)
            exact_best = cursor.fetchone()
            if exact_best:
                gid, gname, desc, romof, cloneof, sys_name, plat, matched_cnt, total_roms = exact_best
                conn.close()
                return _build_arcade_dat_result(gname, desc, romof, cloneof, sys_name, plat, matched_cnt, total_roms)

        # 2. 파일명 stem 기반 조회 (단, internal_crcs가 제공된 경우 최소 1개 이상 CRC가 일치해야 채택)
        if internal_crcs:
            placeholders = ",".join(["?"] * len(internal_crcs))
            cursor.execute(f"""
                SELECT g.id, g.name, g.description, g.romof, g.cloneof, g.system_name, g.platform, COUNT(r.id) as matched_cnt
                FROM games g
                LEFT JOIN roms r ON g.id = r.game_id AND r.crc32 IN ({placeholders})
                WHERE g.name = ?
                GROUP BY g.id
                ORDER BY matched_cnt DESC
                LIMIT 1
            """, internal_crcs + [clean_stem])
            row = cursor.fetchone()
            if row and (row[7] > 0 or not internal_crcs):
                gid, gname, desc, romof, cloneof, sys_name, plat, matched_count = row
                cursor.execute("SELECT COUNT(*) FROM roms WHERE game_id = ?", (gid,))
                cnt_row = cursor.fetchone()
                total_roms = cnt_row[0] if cnt_row else 0
                conn.close()
                return _build_arcade_dat_result(gname, desc, romof, cloneof, sys_name, plat, matched_count, total_roms)
        else:
            cursor.execute("SELECT id, name, description, romof, cloneof, system_name, platform FROM games WHERE name = ?", (clean_stem,))
            row = cursor.fetchone()
            if row:
                gid, gname, desc, romof, cloneof, sys_name, plat = row
                conn.close()
                return _build_arcade_dat_result(gname, desc, romof, cloneof, sys_name, plat, 0, 1)

        # 3. 내부 CRC32 부분 매칭 조회 (단일 롬 또는 멀티 칩 대조)
        if internal_crcs:
            placeholders = ",".join(["?"] * len(internal_crcs))
            query = f"""
                SELECT g.id, g.name, g.description, g.romof, g.cloneof, g.system_name, g.platform, COUNT(r.id) as matched
                FROM roms r
                JOIN games g ON r.game_id = g.id
                WHERE r.crc32 IN ({placeholders})
                GROUP BY g.id
                ORDER BY matched DESC
                LIMIT 1
            """
            cursor.execute(query, internal_crcs)
            best = cursor.fetchone()
            if best and best[7] >= 1:
                gid, gname, desc, romof, cloneof, sys_name, plat, matched_cnt = best
                cursor.execute("SELECT COUNT(*) FROM roms WHERE game_id = ?", (gid,))
                cnt_row = cursor.fetchone()
                total_roms = cnt_row[0] if cnt_row else 0
                conn.close()
                return _build_arcade_dat_result(gname, desc, romof, cloneof, sys_name, plat, matched_cnt, total_roms)

        conn.close()
    except Exception as e:
        logger.debug(f"[{SELF_ID}] Arcade DAT DB query error: {e}")

    return None


def _detect_rom_info(file_path):
    """ROM 파일의 코어(core), 플랫폼(platform), 타이틀(title), 게임코드 등을 자동 감지합니다."""
    info = {
        "core": "",
        "platform": "",
        "title": "",
        "game_code": "",
        "maker_code": "",
        "needed_bios": "",
        "parent_hint": "",
        "required_chd": "",
        "matched_count": 0,
        "total_roms": 0,
        "match_rate": 0.0,
        "serial_code": "",
        "source_system": "filename",
        "metadata_source": "",
        "metadata_confidence": 0,
        "disk_missing_files": [],
        "resolved_disk_files": [],
        "disc_count": 1,
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
                    if _is_bios_file(file_path) or _is_bios_file(clean_fname) or stem in KNOWN_BIOS_STEMS:
                        info["core"] = "_skip_"
                        info["platform"] = "_skip_"
                    else:
                        # 2. 내장 All-In-One DAT DB (Arcade + 콘솔 기종) 초고속 조회
                        internal_crcs = [f"{z.getinfo(n).CRC:08x}".lower() for n in z.namelist() if not n.startswith(".") and z.getinfo(n).file_size > 0]
                        dat_match = _query_arcade_dat(stem, internal_crcs)
                        if dat_match:
                            d_plat = dat_match.get("platform") or "Arcade"
                            if d_plat == "SNES":
                                info["core"] = "snes"
                                info["platform"] = "SNES"
                            elif d_plat == "Genesis":
                                info["core"] = "segaMD"
                                info["platform"] = "Genesis"
                            elif d_plat == "GBA":
                                info["core"] = "gba"
                                info["platform"] = "GBA"
                            elif d_plat == "NES":
                                info["core"] = "nes"
                                info["platform"] = "NES"
                            elif d_plat == "PCE":
                                info["core"] = "pce"
                                info["platform"] = "PCE"
                            elif d_plat == "GameGear":
                                info["core"] = "gamegear"
                                info["platform"] = "GameGear"
                            elif d_plat == "SMS":
                                info["core"] = "segaMS"
                                info["platform"] = "SMS"
                            elif d_plat == "NGP":
                                info["core"] = "ngp"
                                info["platform"] = "NGP"
                            else:
                                is_mame_only = (dat_match.get("system_name") == "MAME2003Plus")
                                info["core"] = "mame2003" if is_mame_only else "arcade"
                                info["platform"] = "Neo-Geo" if (dat_match.get("romof") == "neogeo" or stem in KNOWN_NEOGEO_STEMS) else "Arcade"

                            if dat_match.get("name"):
                                info["game_code"] = dat_match["name"]
                            if dat_match.get("description"):
                                info["title"] = dat_match["description"]
                            info["parent_hint"] = dat_match.get("required_parent") or ""
                            info["needed_bios"] = dat_match.get("required_bios") or ""
                            info["required_chd"] = dat_match.get("required_chd") or ""
                            info["matched_count"] = int(dat_match.get("matched_count") or 0)
                            info["total_roms"] = int(dat_match.get("total_roms") or 0)
                            info["match_rate"] = float(dat_match.get("match_rate") or 0.0)
                        # 3. 내장 아케이드 정적 사전(KNOWN_ARCADE_TITLES) 보조 대조
                        elif stem in KNOWN_ARCADE_TITLES:
                            info["core"] = "arcade"
                            info["platform"] = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"
                            info["title"] = KNOWN_ARCADE_TITLES[stem]
                        else:
                            # 4. ZIP 내부 모든 파일 바이너리 헤더 전수 조사 (메가드라이브, SNES, NES, GBA, GB, N64 등)
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
                                    elif len(sample) >= 0x8000:
                                        is_snes_valid = False
                                        for off in (0x7FC0, 0xFFC0, 0x81C0, 0x101C0):
                                            if len(sample) >= off + 0x20:
                                                c_inv = int.from_bytes(sample[off+0x1C:off+0x1E], "little")
                                                c_sum = int.from_bytes(sample[off+0x1E:off+0x20], "little")
                                                if (c_inv + c_sum) == 0xFFFF:
                                                    is_snes_valid = True
                                                    break
                                        if is_snes_valid:
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
                                # 4. ZIP 내부 파일 목록 검사:
                                # - 내부 파일이 여러 개의 칩셋 덤프(.u1, .bin, .rom 등)로 구성된 경우 무조건 아케이드 기판으로 분류
                                non_meta_files = [n for n in z.namelist() if not n.startswith(".") and z.getinfo(n).file_size > 0]
                                is_multi_chipset = len(non_meta_files) >= 2 or any(re.search(r"\.(u\d+|ic\d+|p\d+|c\d+|v\d+|s\d+|m\d+|\d{3})$", n.lower()) for n in non_meta_files)

                                if is_multi_chipset:
                                    info["core"] = "arcade"
                                    info["platform"] = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"
                                else:
                                    # 단일 파일일 때만 상위 디렉터리명 폴백 적용
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
                with py7zr.SevenZipFile(file_path, mode="r") as z7:
                    file_list = z7.list()
                    extracted_names = [zf.filename for zf in file_list if not zf.is_directory and not zf.filename.startswith(".")]

                    matching = []
                    for fname in extracted_names:
                        e = os.path.splitext(fname)[1].lower()
                        if e in SUPPORTED_SYSTEMS:
                            matching.append((fname, e))

                    if matching:
                        inner_name, inner_ext = matching[0]
                        sys_info = SUPPORTED_SYSTEMS[inner_ext]
                        info["core"] = sys_info["core"]
                        info["platform"] = sys_info["platform"]
                        base_inner = os.path.splitext(os.path.basename(inner_name))[0]
                        clean_inner = re.sub(r"[\(\[\{].*?[\)\]\}]", "", base_inner).strip()
                        if clean_inner:
                            info["title"] = clean_inner
                    elif extracted_names:
                        stem = os.path.splitext(os.path.basename(file_path))[0].lower()
                        internal_crcs = [f"{zf.crc32:08x}".lower() for zf in file_list if zf.crc32]

                        dat_match = _query_arcade_dat(stem, internal_crcs)
                        if dat_match:
                            d_plat = dat_match.get("platform") or "Arcade"
                            if d_plat == "SNES":
                                info["core"] = "snes"
                                info["platform"] = "SNES"
                            elif d_plat == "Genesis":
                                info["core"] = "segaMD"
                                info["platform"] = "Genesis"
                            elif d_plat == "GBA":
                                info["core"] = "gba"
                                info["platform"] = "GBA"
                            elif d_plat == "NES":
                                info["core"] = "nes"
                                info["platform"] = "NES"
                            elif d_plat == "PCE":
                                info["core"] = "pce"
                                info["platform"] = "PCE"
                            elif d_plat == "GameGear":
                                info["core"] = "gamegear"
                                info["platform"] = "GameGear"
                            elif d_plat == "SMS":
                                info["core"] = "segaMS"
                                info["platform"] = "SMS"
                            elif d_plat == "NGP":
                                info["core"] = "ngp"
                                info["platform"] = "NGP"
                            else:
                                is_mame_only = (dat_match.get("system_name") == "MAME2003Plus")
                                info["core"] = "mame2003" if is_mame_only else "arcade"
                                info["platform"] = "Neo-Geo" if (dat_match.get("romof") == "neogeo" or stem in KNOWN_NEOGEO_STEMS) else "Arcade"

                            if dat_match.get("description"):
                                info["title"] = dat_match["description"]
                            info["parent_hint"] = dat_match.get("required_parent") or ""
                            info["needed_bios"] = dat_match.get("required_bios") or ""
                            info["required_chd"] = dat_match.get("required_chd") or ""
                            info["matched_count"] = int(dat_match.get("matched_count") or 0)
                            info["total_roms"] = int(dat_match.get("total_roms") or 0)
                            info["match_rate"] = float(dat_match.get("match_rate") or 0.0)
                        else:
                            # 2. 콘솔 헤더 전수 분석
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

                            if not info["core"] or info["core"] == "gba":
                                if stem in KNOWN_ARCADE_TITLES or stem in KNOWN_NEOGEO_STEMS:
                                    info["core"] = "arcade"
                                    info["platform"] = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"
                                    info["title"] = KNOWN_ARCADE_TITLES.get(stem, stem)
                                    if stem in KNOWN_NEOGEO_STEMS:
                                        info["needed_bios"] = "neogeo.zip"
                            info["title"] = KNOWN_ARCADE_TITLES.get(stem, info.get("title") or stem)
        except Exception as e:
            logger.debug(f"[{SELF_ID}] 7z inspect error: {e}")
    else:
        if ext in SUPPORTED_SYSTEMS:
            sys_info = SUPPORTED_SYSTEMS[ext]
            info["core"] = sys_info["core"]
            info["platform"] = sys_info["platform"]
        elif ext in DISK_IMAGE_EXTS:
            disk_details = _resolve_disk_sidecars(file_path)
            info["disk_missing_files"] = disk_details.get("missing_files") or []
            info["resolved_disk_files"] = disk_details.get("resolved_files") or []
            info["disc_count"] = int(disk_details.get("disc_count") or 1)
            path_lower = os.path.abspath(file_path).lower().replace("\\", "/")
            # CD/디스크 이미지 시리얼 스캔 (PS1 / PBP 등)
            serial = disk_details.get("serial_code") or ""
            if serial:
                serial_upper = serial.upper()
                if serial_upper.startswith(("MK", "GS", "T-", "GS-")) or "T-" in serial_upper:
                    info["core"] = "saturn"
                    info["platform"] = "Saturn"
                    info["needed_bios"] = "saturn_bios.bin"
                elif ext == ".chd" and any(tok in os.path.basename(file_path).lower() for tok in ("pce", "tg16", "supercd", "pcengine")):
                    info["core"] = "pce"
                    info["platform"] = "PCECD"
                    info["needed_bios"] = "syscard3.pce"
                else:
                    info["core"] = "psx"
                    info["platform"] = "PS1"
                    info["needed_bios"] = "scph5501.bin"
                info["game_code"] = serial
                info["serial_code"] = serial
                info["source_system"] = "serial"
            elif ext == ".gdi":
                info["core"] = "dreamcast"
                info["platform"] = "Dreamcast"
                info["source_system"] = "sidecar"
            elif ext == ".cue" and (info["resolved_disk_files"] or info["disk_missing_files"]):
                info["source_system"] = "sidecar"
                if any(tok in path_lower for tok in ("/saturn/", "/segasaturn/")):
                    info["core"] = "saturn"
                    info["platform"] = "Saturn"
                    info["needed_bios"] = "saturn_bios.bin"
                else:
                    info["core"] = "psx"
                    info["platform"] = "PS1"
                    info["needed_bios"] = "scph5501.bin"
        try:
            with open(file_path, "rb") as f:
                raw_data = f.read(0x10000)
        except Exception:
            pass

    # 플랫폼별 세부 헤더 타이틀 추출
    if info["core"] == "snes":
        if raw_data and len(raw_data) >= 0x8000:
            for offset in (0x7FC0, 0xFFC0, 0x81C0, 0x101C0):
                if len(raw_data) >= offset + 0x20:
                    c_inv = raw_data[offset+0x1C:offset+0x1E]
                    c_sum = raw_data[offset+0x1E:offset+0x20]
                    inv_val = int.from_bytes(c_inv, "little")
                    sum_val = int.from_bytes(c_sum, "little")
                    # SNES 공식 규격: Checksum Complement + Checksum == 0xFFFF
                    if (inv_val + sum_val) == 0xFFFF:
                        candidate = raw_data[offset:offset+21]
                        clean = "".join(chr(b) for b in candidate if 32 <= b <= 126).strip()
                        if _is_valid_header_title(clean):
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
            if _is_valid_header_title(title): info["title"] = title
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
            if _is_valid_header_title(n64_title):
                info["title"] = n64_title
    elif info["core"] == "segaMD":
        if raw_data and len(raw_data) >= 0x150:
            # 0x120:0x150 위치의 일본/해외 공식 게임명
            raw_title = raw_data[0x120:0x150]
            md_title = "".join(chr(b) for b in raw_title if 32 <= b <= 126).strip()
            if _is_valid_header_title(md_title):
                info["title"] = md_title

    # 최종 타이틀 검증 (유효하지 않은 깨진 바이너리 찌꺼기만 제거)
    if info.get("title") and not _is_valid_header_title(info.get("title", "")):
        # 만약 한글이 포함되어 있다면 유효한 타이틀로 인정
        if not re.search(r"[가-힣]", info.get("title", "")):
            info["title"] = ""

    # 기종 및 롬셋 기반 필요 바이오스(needed_bios) 자동 동적 판별
    f_lower = os.path.basename(file_path).lower()
    f_stem = os.path.splitext(f_lower)[0].lower()
    c_lower = (info.get("core") or "").lower()
    p_lower = (info.get("platform") or "").lower()

    if p_lower == "neo-geo" or (c_lower == "arcade" and any(f_stem.startswith(k) for k in ("mslug", "kof", "samsho", "fatfur", "garou", "aof", "lastblad", "neogeo", "snk", "rbff", "spinmast", "maglord", "pulstar", "blazstar"))):
        info["needed_bios"] = "neogeo.zip"
    elif c_lower == "arcade" and any(f_stem.startswith(k) for k in ("olds", "kov", "orlegend", "dmnfrnt", "martmast")):
        info["needed_bios"] = "pgm.zip"
    elif c_lower == "arcade" and any(f_stem.startswith(k) for k in ("bldyror", "brvblade", "sfex", "rvschool", "starglad", "strider2", "techromn", "jgts", "raiden2", "raidendx")):
        info["needed_bios"] = "acpsx.zip"
    elif c_lower == "psx" or p_lower == "ps1":
        info["needed_bios"] = "scph5501.bin"
    elif p_lower == "fds" or f_lower.endswith(".fds"):
        info["needed_bios"] = "disksys.rom"
    elif c_lower == "pce" or p_lower == "pce":
        info["needed_bios"] = "syscard3.pce"
    elif c_lower == "segacd" or p_lower == "segacd":
        info["needed_bios"] = "bios_cd_u.bin"
    elif c_lower == "saturn" or p_lower == "saturn":
        info["needed_bios"] = "saturn_bios.bin"
    elif c_lower == "3do" or p_lower == "3do":
        info["needed_bios"] = "3dobios.rom"

    if c_lower in ("arcade", "mame2003") and not info.get("parent_hint"):
        clone_match = re.match(r"^([a-z0-9_]+?)([juka-e1-3])$", f_stem, re.IGNORECASE)
        if clone_match and len(clone_match.group(1)) >= 3:
            info["parent_hint"] = f"{clone_match.group(1).lower()}.zip"

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
            "arcade_dat.db",
            "rom_analysis_adapter.py",
            "libs/rom_analyzer/VENDORED_FROM.json",
            "libs/rom_analyzer/__init__.py",
            "libs/rom_analyzer/analyzer.py",
            "libs/rom_analyzer/arcade/__init__.py",
            "libs/rom_analyzer/arcade/bios_db.py",
            "libs/rom_analyzer/arcade/dat_matcher.py",
            "libs/rom_analyzer/arcade/database.py",
            "libs/rom_analyzer/arcade/detector.py",
            "libs/rom_analyzer/core_info.py",
            "libs/rom_analyzer/data/arcade_dat.db",
            "libs/rom_analyzer/data/rom_metadata.db",
            "libs/rom_analyzer/db.py",
            "libs/rom_analyzer/disc/__init__.py",
            "libs/rom_analyzer/disc/inspector.py",
            "libs/rom_analyzer/disc/parsers/__init__.py",
            "libs/rom_analyzer/disc/parsers/chd.py",
            "libs/rom_analyzer/disc/parsers/common.py",
            "libs/rom_analyzer/disc/parsers/cue.py",
            "libs/rom_analyzer/disc/parsers/gdi.py",
            "libs/rom_analyzer/disc/parsers/m3u.py",
            "libs/rom_analyzer/disc/parsers/pbp.py",
            "libs/rom_analyzer/disc/serial_scanner.py",
            "libs/rom_analyzer/emulatorjs_config.py",
            "libs/rom_analyzer/evidence.py",
            "libs/rom_analyzer/headers/__init__.py",
            "libs/rom_analyzer/headers/detector.py",
            "libs/rom_analyzer/headers/misc.py",
            "libs/rom_analyzer/headers/nintendo.py",
            "libs/rom_analyzer/headers/sega.py",
            "libs/rom_analyzer/models.py",
            "libs/rom_analyzer/providers/__init__.py",
            "libs/rom_analyzer/providers/base.py",
            "libs/rom_analyzer/providers/igdb.py",
            "libs/rom_analyzer/providers/libretro.py",
            "libs/rom_analyzer/providers/screenscraper.py",
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


    def _get_emulatorjs_root(self):
        """통합 에뮬레이터 라이브러리 루트 디렉터리 (EMULATORJS_ROOT 또는 기본 /mnt/gdrive/emulatorjs)"""
        custom_root = self._get_setting("EMULATORJS_ROOT", "").strip()
        if custom_root:
            return custom_root
        # 레거시 EXTRA_ROMS_PATH 등에서 상위 루트 유추
        extra_p = self._get_setting("EXTRA_ROMS_PATH", "").strip()
        if extra_p and "/roms" in extra_p:
            cand = extra_p.split("/roms")[0]
            if os.path.isdir(cand):
                return cand
        if os.path.isdir("/mnt/gdrive/emulatorjs"):
            return "/mnt/gdrive/emulatorjs"
        return self._get_data_dir()

    def _get_roms_dir(self):
        """기본 롬 파일 디렉터리 (../../data/bookoasis_gamebooks/roms/)"""
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
        """커버 아트 이미지 디렉터리"""
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

    def _cover_platform_prefixes(self, platform_or_core):
        key = str(platform_or_core or "").lower().replace("-", "").replace("_", "")
        groups = {
            "arcade": ["roms_arcade_", "roms_mame2003_", "library_arcade_roms_", "roms_neogeo_"],
            "mame2003": ["roms_mame2003_", "roms_arcade_", "library_arcade_roms_", "roms_neogeo_"],
            "neogeo": ["roms_neogeo_", "roms_arcade_", "roms_mame2003_", "library_arcade_roms_"],
            "genesis": ["roms_megadriv_", "roms_segamd_", "roms_genesis_"],
            "segamd": ["roms_megadriv_", "roms_segamd_", "roms_genesis_"],
            "snes": ["roms_snes_"], "nes": ["roms_nes_"], "gba": ["roms_gba_"],
            "gb": ["roms_gb_"], "gbc": ["roms_gbc_"], "n64": ["roms_n64_"],
            "ps1": ["roms_psx_", "roms_ps1_"], "psx": ["roms_psx_", "roms_ps1_"],
            "pce": ["roms_pce_"], "saturn": ["roms_saturn_", "roms_segasaturn_"],
        }
        return groups.get(key, [])

    def _resolve_existing_cover(self, game_id, filename="", platform_or_core="", current_cover_path="", update_db=False):
        """경로 기반 game_id가 바뀐 경우에도 기존 커버 파일을 안전하게 재연결한다."""
        if current_cover_path and os.path.isfile(current_cover_path):
            return current_cover_path
        covers_dir = self._get_covers_dir()
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            exact = os.path.join(covers_dir, f"{game_id}{ext}")
            if os.path.isfile(exact):
                if update_db:
                    self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (exact, game_id))
                return exact
        raw_filename = os.path.basename(str(filename or "")).strip().lower()
        raw_stem = os.path.splitext(raw_filename)[0].strip()
        if not raw_stem:
            return None
        prefixes = self._cover_platform_prefixes(platform_or_core)
        candidates = []
        try:
            for entry in os.scandir(covers_dir):
                if not entry.is_file():
                    continue
                name = entry.name.lower()
                if os.path.splitext(name)[1] not in (".png", ".jpg", ".jpeg", ".webp"):
                    continue
                if raw_filename not in name and raw_stem not in name:
                    continue
                if prefixes and not any(name.startswith(prefix) for prefix in prefixes):
                    continue
                score = 100 if raw_filename and raw_filename in name else 40
                for idx, prefix in enumerate(prefixes):
                    if name.startswith(prefix):
                        score += max(1, 30 - idx)
                        break
                if name.startswith("roms_"):
                    score += 10
                candidates.append((score, entry.path))
        except Exception as e:
            logger.debug(f"[{SELF_ID}] Existing cover fallback scan error ({filename}): {e}")
            return None
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1].lower()))
        best_path = candidates[0][1]
        if update_db:
            self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (best_path, game_id))
        return best_path

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
        """시스템 바이오스 파일 디렉터리"""
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

    def _get_runtime_bios_candidates(self, required_bios):
        req = _normalize_required_archive(required_bios)
        if not req:
            return []

        candidates = [req]
        if req.startswith("scph") and req.endswith(".bin"):
            candidates.extend(["scph5501.bin", "scph1001.bin", "scph5500.bin", "scph5502.bin", "scph7001.bin"])
        elif req == "bios_cd_u.bin":
            candidates.extend(["bios_cd_u.bin", "bios_cd_j.bin", "bios_cd_e.bin"])

        unique = []
        seen = set()
        for name in candidates:
            safe_name = os.path.basename(str(name or "").strip())
            lower_name = safe_name.lower()
            if safe_name and lower_name not in seen:
                unique.append(safe_name)
                seen.add(lower_name)
        return unique

    def _iter_runtime_bios_dirs(self, game_file_path=None, game_id=None):
        resolved_game_path = game_file_path or ""
        if not resolved_game_path and game_id:
            try:
                rows = self._db_query("SELECT file_path FROM games WHERE id = ?", (game_id,))
                if rows:
                    resolved_game_path = rows[0].get("file_path") or ""
            except Exception:
                resolved_game_path = ""

        search_dirs = []
        if resolved_game_path:
            game_dir = os.path.dirname(os.path.abspath(resolved_game_path))
            if game_dir:
                search_dirs.append(game_dir)
                # platform/roms 옆의 platform/bios 디렉터리도 자동 탐색
                if os.path.basename(game_dir).lower() == "roms":
                    plat_bios = os.path.join(os.path.dirname(game_dir), "bios")
                    if os.path.isdir(plat_bios):
                        search_dirs.append(plat_bios)

        search_dirs.append(self._get_bios_dir())
        search_dirs.append(self._get_roms_dir())
        extra_p = self._get_setting("EXTRA_ROMS_PATH", "").strip()
        if extra_p and os.path.isdir(extra_p):
            search_dirs.append(extra_p)

        seen = set()
        for sdir in search_dirs:
            if not sdir or not os.path.isdir(sdir):
                continue
            abs_dir = os.path.abspath(sdir)
            if abs_dir in seen:
                continue
            seen.add(abs_dir)
            yield abs_dir

    def _find_runtime_bios_path(self, required_bios, game_file_path=None, game_id=None):
        candidate_names = self._get_runtime_bios_candidates(required_bios)
        if not candidate_names:
            return None

        for sdir in self._iter_runtime_bios_dirs(game_file_path=game_file_path, game_id=game_id):
            for candidate in candidate_names:
                exact_path = os.path.join(sdir, candidate)
                if os.path.isfile(exact_path):
                    return exact_path

            try:
                lower_map = {str(f).lower(): f for f in os.listdir(sdir)}
            except Exception:
                lower_map = {}

            for candidate in candidate_names:
                actual_name = lower_map.get(candidate.lower())
                if actual_name:
                    actual_path = os.path.join(sdir, actual_name)
                    if os.path.isfile(actual_path):
                        return actual_path
        return None

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
                        cover_path TEXT,
                        needed_bios TEXT
                    )"""
                )
                for col, ctype in (
                    ("core", "TEXT DEFAULT 'gba'"),
                    ("platform", "TEXT DEFAULT 'GBA'"),
                    ("mtime", "REAL DEFAULT 0"),
                    ("needed_bios", "TEXT"),
                    ("health_status", "TEXT DEFAULT 'pass'"),
                    ("missing_roms", "TEXT DEFAULT ''"),
                    ("rom_crc32", "TEXT DEFAULT ''"),
                    ("rom_md5", "TEXT DEFAULT ''"),
                    ("rom_sha1", "TEXT DEFAULT ''"),
                    ("serial_code", "TEXT DEFAULT ''"),
                    ("normalized_title", "TEXT DEFAULT ''"),
                    ("source_system", "TEXT DEFAULT ''"),
                    ("metadata_source", "TEXT DEFAULT ''"),
                    ("metadata_confidence", "INTEGER DEFAULT 0"),
                    ("canonical_title", "TEXT DEFAULT ''"),
                    ("alt_titles", "TEXT DEFAULT ''"),
                    ("region", "TEXT DEFAULT ''"),
                    ("genre", "TEXT DEFAULT ''"),
                    ("developer", "TEXT DEFAULT ''"),
                    ("publisher", "TEXT DEFAULT ''"),
                    ("release_year", "TEXT DEFAULT ''"),
                    ("players", "INTEGER DEFAULT 0"),
                    ("description", "TEXT DEFAULT ''"),
                    ("region_tag", "TEXT DEFAULT ''"),
                    ("revision_tag", "TEXT DEFAULT ''"),
                    ("disc_number", "INTEGER DEFAULT 0"),
                    ("content_flags", "TEXT DEFAULT ''"),
                ):
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
    def _get_db_conn(self, timeout=60):
        db_path = self._get_db_path()
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _db_query(self, query, args=()):
        with _DB_LOCK:
            conn = self._get_db_conn(timeout=60)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute(query, args)
                rows = [dict(r) for r in cur.fetchall()]
                return rows
            finally:
                conn.close()

    def _db_execute(self, query, args=()):
        with _DB_LOCK:
            conn = self._get_db_conn(timeout=60)
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
            current_cover = rows[0]["cover_path"] if rows else ""
            existing_cover = self._resolve_existing_cover(
                game_id, filename, platform_or_core, current_cover_path=current_cover, update_db=True
            )
            if existing_cover:
                return existing_cover

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
                metadata_payload = _fetch_screenscraper_metadata(file_path, platform_or_core, filename, sc_conf)
                if metadata_payload:
                    self._db_execute(
                        "UPDATE games SET canonical_title = COALESCE(NULLIF(canonical_title, ''), ?), alt_titles = COALESCE(NULLIF(alt_titles, ''), ?), region = COALESCE(NULLIF(region, ''), ?), genre = COALESCE(NULLIF(genre, ''), ?), developer = COALESCE(NULLIF(developer, ''), ?), publisher = COALESCE(NULLIF(publisher, ''), ?), release_year = COALESCE(NULLIF(release_year, ''), ?), players = CASE WHEN players IS NULL OR players = 0 THEN ? ELSE players END, description = COALESCE(NULLIF(description, ''), ?), metadata_source = ?, metadata_confidence = CASE WHEN metadata_confidence IS NULL OR metadata_confidence < ? THEN ? ELSE metadata_confidence END WHERE id = ?",
                        (
                            metadata_payload.get("canonical_title") or "",
                            metadata_payload.get("alt_titles") or "",
                            metadata_payload.get("region") or "",
                            metadata_payload.get("genre") or "",
                            metadata_payload.get("developer") or "",
                            metadata_payload.get("publisher") or "",
                            metadata_payload.get("release_year") or "",
                            metadata_payload.get("players") or 0,
                            metadata_payload.get("description") or "",
                            metadata_payload.get("metadata_source") or "screenscraper",
                            metadata_payload.get("metadata_confidence") or 0,
                            metadata_payload.get("metadata_confidence") or 0,
                            game_id,
                        ),
                    )
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
                igdb_meta = _fetch_igdb_metadata(search_title, platform_or_core, igdb_conf)
                if igdb_meta:
                    self._db_execute(
                        "UPDATE games SET genre = COALESCE(NULLIF(genre, ''), ?), developer = COALESCE(NULLIF(developer, ''), ?), publisher = COALESCE(NULLIF(publisher, ''), ?), description = COALESCE(NULLIF(description, ''), ?), release_year = COALESCE(NULLIF(release_year, ''), ?), metadata_source = COALESCE(NULLIF(metadata_source, ''), ?), metadata_confidence = CASE WHEN metadata_confidence IS NULL OR metadata_confidence < ? THEN ? ELSE metadata_confidence END WHERE id = ?",
                        (
                            igdb_meta.get("genre") or "",
                            igdb_meta.get("developer") or "",
                            igdb_meta.get("publisher") or "",
                            igdb_meta.get("description") or "",
                            igdb_meta.get("release_year") or "",
                            igdb_meta.get("metadata_source") or "igdb",
                            igdb_meta.get("metadata_confidence") or 0,
                            igdb_meta.get("metadata_confidence") or 0,
                            game_id,
                        ),
                    )
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
        raw_name = _strip_romm_name_prefix(os.path.splitext(filename)[0])
        header_title = rom_info.get("title") if _is_valid_header_title(rom_info.get("title")) else ""
        raw_title = header_title or (game.get("title") if _is_valid_header_title(game.get("title")) else "") or raw_name
        clean_title = _resolve_korean_game_title(filename, raw_title)
        needed_bios = rom_info.get("needed_bios") or ""
        if clean_title and clean_title != game.get("title"):
            self._db_execute("UPDATE games SET title = ?, needed_bios = ? WHERE id = ?", (clean_title, needed_bios, game_id))
            game["title"] = clean_title
            game["needed_bios"] = needed_bios
        elif needed_bios != (game.get("needed_bios") or ""):
            self._db_execute("UPDATE games SET needed_bios = ? WHERE id = ?", (needed_bios, game_id))
            game["needed_bios"] = needed_bios

        cover_path = self._resolve_existing_cover(
            game_id,
            filename,
            core,
            current_cover_path=game.get("cover_path") or "",
            update_db=True,
        ) or ""
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

    def _scan_roms(self, new_only=False, force_full=False):
        """기본 roms 폴더 및 설정된 추가 경로의 ROM을 스캔하여 DB에 동기화합니다.
        - new_only: 새로 발견된 게임만 등록 및 커버 검색
        - force_full: 기존 캐시를 건너뛰지 않고 모든 롬의 헤더/DAT DB를 100% 처음부터 전수 재분석
        """
        self._migrate_bios_files()

        scan_dirs = [self._get_roms_dir()]
        extra_path = self._get_setting("EXTRA_ROMS_PATH", "").strip()
        if extra_path and os.path.isdir(extra_path) and extra_path not in scan_dirs:
            scan_dirs.append(extra_path)

        bios_dir = os.path.abspath(self._get_bios_dir())
        covers_dir = os.path.abspath(self._get_covers_dir())

        allowed_exts = set(SUPPORTED_SYSTEMS.keys()) | {".zip", ".7z", ".cue", ".gdi"}
        found_files = {}

        for sdir in scan_dirs:
            try:
                for root, dirs, files in os.walk(sdir):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    abs_root = os.path.abspath(root)
                    # 바이오스 폴더 또는 커버 폴더 하위 경로는 롬 스캔 대상에서 원천 제외
                    if abs_root.startswith(bios_dir) or abs_root.startswith(covers_dir):
                        dirs[:] = []
                        continue
                    rel_root = os.path.relpath(abs_root, os.path.abspath(sdir))
                    if rel_root != "." and any(part.startswith(".") for part in rel_root.split(os.sep)):
                        continue

                    for f in files:
                        if f.startswith("."):
                            continue
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
                                    "sdir": sdir,
                                }
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"[{SELF_ID}] Scan dir error ({sdir}): {e}")

        existing_games = {g["id"]: g for g in self._db_query("SELECT * FROM games")}
        now_str = _get_kst_now_str()
        available_rom_names = {str(v.get("filename") or "").lower() for v in found_files.values() if v.get("filename")}
        available_bios_names = set()
        bios_dir = self._get_bios_dir()
        if os.path.isdir(bios_dir):
            try:
                available_bios_names = {f.lower() for f in os.listdir(bios_dir) if not f.startswith(".")}
            except Exception:
                available_bios_names = set()

        # 병렬 분석이 필요한 파일 목록 선별
        files_to_process = []
        covers_to_fetch = []
        new_games_added = []
        deleted_count = 0

        total_files = len(found_files)
        _update_scan_progress(current=0, total=total_files, current_file="스캔 준비 중...", status="scanning", is_running=True)

        for gid, info in found_files.items():
            existing = existing_games.get(gid)
            if existing and not force_full:
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

        processed_counter = [0]
        total_to_process = len(files_to_process)

        def _process_single_rom(info):
            try:
                gid = info["id"]
                rom_info = _detect_rom_info(info["file_path"])
                with _SCAN_PROGRESS_LOCK:
                    processed_counter[0] += 1
                    _SCAN_PROGRESS["current"] = processed_counter[0]
                    _SCAN_PROGRESS["total"] = total_to_process
                    _SCAN_PROGRESS["current_file"] = info["filename"]
                    _SCAN_PROGRESS["updated_at"] = time.time()

                if rom_info.get("platform") == "_skip_":
                    return None

                raw_name = _strip_romm_name_prefix(os.path.splitext(info["filename"])[0])
                header_title = rom_info.get("title") or ""
                mapped_header = KNOWN_N64_NAMES.get(header_title.upper().replace("_", " ").replace("-", " ").strip()) or KNOWN_N64_NAMES.get(header_title.upper()) or header_title

                # 파일명이 이미 온전한 게임명(예: "Breath of Fire 1 (K)", "Langrisser II...") 형태인 경우 파일명을 최우선 사용
                # 파일명이 단순 단축어(예: 1944, ddanpei, wiz 등)인 경우에만 DAT/헤더 타이틀 사용
                clean_raw = re.sub(r"[\(\[\{].*?[\)\]\}]", "", raw_name).strip()
                if len(clean_raw) >= 4 and not re.match(r"^[a-z0-9_]{1,7}$", clean_raw.lower()):
                    target_for_kor = raw_name
                else:
                    target_for_kor = mapped_header or raw_name

                clean_title = _resolve_korean_game_title(info["filename"], target_for_kor)
                disk_missing_files = rom_info.get("disk_missing_files") or []
                if disk_missing_files and str(rom_info.get("source_system") or "").strip() in ("", "filename"):
                    rom_info["source_system"] = "sidecar"
                identity_info = _collect_identity_fields(info["file_path"], rom_info, clean_title, info["size_bytes"])

                return {
                    "gid": gid,
                    "info": info,
                    "rom_info": rom_info,
                    "clean_title": clean_title,
                    "mapped_header": mapped_header,
                    "identity_info": identity_info,
                }
            except Exception as ex:
                logger.debug(f"[{SELF_ID}] Process single rom error ({info.get('filename')}): {ex}")
                return None

        # ThreadPoolExecutor를 이용한 멀티스레드 병렬 바이너리 분석
        if files_to_process:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(_process_single_rom, files_to_process))

            _update_scan_progress(current=total_files, total=total_files, current_file="데이터베이스 동기화 중...", status="saving", is_running=True)

            covers_dir = self._get_covers_dir()

            for res in results:
                if not res:
                    continue
                gid = res["gid"]
                info = res["info"]
                rom_info = res["rom_info"]
                clean_title = res["clean_title"]
                mapped_header = res["mapped_header"]
                identity_info = res.get("identity_info") or {}

                curr_file_path = info["file_path"]
                curr_dir = os.path.dirname(os.path.abspath(curr_file_path))
                base_dir = os.path.dirname(curr_dir)

                target_core_folder = (rom_info.get("core") or rom_info.get("platform") or "other").lower()
                target_core_folder = re.sub(r"[^a-zA-Z0-9_\-]", "_", target_core_folder).strip() or "other"
                current_folder_name = os.path.basename(curr_dir).lower()

                # 모든 .7z 압축 롬 파일: 브라우저 EmulatorJS 호환성 및 안정적 구동을 위해 표준 .zip으로 영구 자동 변환
                f_ext = os.path.splitext(info["filename"])[1].lower()
                if f_ext == ".7z":
                    try:
                        import py7zr
                        zip_fname = os.path.splitext(info["filename"])[0] + ".zip"
                        ideal_dir = os.path.join(base_dir, target_core_folder)
                        os.makedirs(ideal_dir, exist_ok=True)
                        dest_zip_path = os.path.join(ideal_dir, zip_fname)
                        work_zip_path = dest_zip_path + ".part"
                        with tempfile.TemporaryDirectory() as tmpdir:
                            _safe_7z_extract(curr_file_path, tmpdir)
                            extracted_files = []
                            with zipfile.ZipFile(work_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                                for root, _dirs, files in os.walk(tmpdir):
                                    for ef in files:
                                        full_ef = os.path.join(root, ef)
                                        arc_ef = os.path.relpath(full_ef, tmpdir).replace(os.sep, "/")
                                        if arc_ef.startswith("."):
                                            continue
                                        extracted_files.append(arc_ef)
                                        zout.write(full_ef, arc_ef)
                                    if rom_info.get("platform") == "Neo-Geo" or rom_info.get("needed_bios") == "neogeo.zip":
                                        bios_p = os.path.join(self._get_bios_dir(), "neogeo.zip")
                                        if os.path.exists(bios_p):
                                            with zipfile.ZipFile(bios_p, "r") as zb:
                                                existing = set(extracted_files)
                                                for b_info in zb.infolist():
                                                    if b_info.filename not in existing and not b_info.filename.startswith("."):
                                                        zout.writestr(b_info.filename, zb.read(b_info.filename))
                        if not _validate_zip_file(work_zip_path):
                            raise ValueError("converted ZIP validation failed")
                        os.replace(work_zip_path, dest_zip_path)
                        if os.path.exists(curr_file_path):
                            os.remove(curr_file_path)
                        available_rom_names.discard(str(info.get("filename") or "").lower())
                        curr_file_path = dest_zip_path
                        info["file_path"] = dest_zip_path
                        info["filename"] = zip_fname
                        available_rom_names.add(zip_fname.lower())
                        info["size_bytes"] = os.path.getsize(dest_zip_path)
                        info["mtime"] = os.path.getmtime(dest_zip_path)

                        sdir = info.get("sdir") or base_dir
                        rel = os.path.relpath(dest_zip_path, sdir)
                        new_gid = _sanitize_id(f"{os.path.basename(sdir)}_{rel}")
                        if gid in found_files:
                            del found_files[gid]
                        found_files[new_gid] = info
                        gid = new_gid
                    except Exception as conv_ex:
                        logger.error(f"[{SELF_ID}] Scan 7z convert error: {conv_ex}")
                elif f_ext in (".cue", ".gdi") or (current_folder_name != target_core_folder and current_folder_name not in ("roms", "")):
                    try:
                        ideal_dir = os.path.join(base_dir, target_core_folder)
                        os.makedirs(ideal_dir, exist_ok=True)
                        original_path = curr_file_path
                        move_result = _move_disk_bundle(curr_file_path, ideal_dir, related_infos=found_files.values())
                        dest_file_path = move_result.get("primary_path") or original_path
                        if move_result.get("moved") and dest_file_path != original_path:
                            curr_file_path = dest_file_path
                            info["file_path"] = dest_file_path
                            info["filename"] = os.path.basename(dest_file_path)

                            sdir = info.get("sdir") or base_dir
                            rel = os.path.relpath(dest_file_path, sdir)
                            new_gid = _sanitize_id(f"{os.path.basename(sdir)}_{rel}")

                            for c_ext in (".png", ".jpg", ".jpeg", ".webp"):
                                old_c = os.path.join(covers_dir, f"{gid}{c_ext}")
                                if os.path.exists(old_c):
                                    new_c = os.path.join(covers_dir, f"{new_gid}{c_ext}")
                                    try:
                                        shutil.move(old_c, new_c)
                                    except Exception:
                                        pass
                                    break

                            if gid in found_files:
                                del found_files[gid]
                            found_files[new_gid] = info
                            gid = new_gid
                        elif move_result.get("conflict"):
                            logger.warning(f"[{SELF_ID}] Skip disk bundle move due to existing target: {move_result.get('conflict')}")
                    except Exception as move_ex:
                        logger.error(f"[{SELF_ID}] Move rom to ideal folder error: {move_ex}")

                # 아케이드 기판 롬셋의 필수 바이오스 자동 인팩(In-pack) 병합:
                # WebAssembly MAME/FBNeo 코어가 브라우저에서 외부 바이오스 파일을 분리 마운트하지 못해
                # 레트로아크 메인메뉴 화면으로 튕기는 문제를 방지하기 위해, 필요한 바이오스(neogeo.zip, playch10.zip, pgm.zip 등)가 있으면
                # 롬 ZIP 내부에 바이오스 칩셋들을 무손실 자동 패키징 병합
                needed_bios = rom_info.get("needed_bios") or ""
                if not needed_bios and (rom_info.get("platform") == "Neo-Geo" or rom_info.get("core") == "arcade"):
                    if any(k in info["filename"].lower() for k in ("kof", "mslug", "samsho", "fatfur", "garou", "neogeo", "aof", "rbff")):
                        needed_bios = "neogeo.zip"

                if needed_bios and curr_file_path.endswith(".zip") and os.path.exists(curr_file_path):
                    bios_path = os.path.join(self._get_bios_dir(), needed_bios)
                    if os.path.exists(bios_path):
                        try:
                            with zipfile.ZipFile(curr_file_path, "r") as z_cur:
                                existing_names = set(z_cur.namelist())
                            
                            with zipfile.ZipFile(bios_path, "r") as z_bios:
                                bios_missing = [b for b in z_bios.infolist() if b.filename not in existing_names and not b.filename.startswith(".")]
                                if bios_missing:
                                    with zipfile.ZipFile(curr_file_path, "a") as z_cur_append:
                                        for b_item in bios_missing:
                                            z_cur_append.writestr(b_item.filename, z_bios.read(b_item.filename))
                                    info["size_bytes"] = os.path.getsize(curr_file_path)
                                    info["mtime"] = os.path.getmtime(curr_file_path)
                        except Exception as merge_err:
                            logger.debug(f"[{SELF_ID}] Bios merge error during scan: {merge_err}")

                # 무결성 자동 진단 (Health Status 계산)
                health_status = "pass"
                missing_roms_str = ""
                stem = os.path.splitext(info["filename"])[0].lower()
                r_core = (rom_info.get("core") or "").lower()
                r_plat = rom_info.get("platform") or ""
                gcode = rom_info.get("game_code") or ""
                required_bios = _normalize_required_archive(rom_info.get("needed_bios") or "")
                required_parent = _normalize_required_archive(rom_info.get("parent_hint") or "")
                required_chd = rom_info.get("required_chd") or ""
                is_arcade = (r_core in ("arcade", "mame2003") or r_plat in ("Arcade", "Neo-Geo"))
                bios_ready = _is_required_bios_available(required_bios, available_bios_names, bios_dir)

                disk_missing_files = rom_info.get("disk_missing_files") or []
                if disk_missing_files:
                    health_status = "incomplete"
                    missing_roms_str = json.dumps(disk_missing_files[:6], ensure_ascii=False)
                elif required_chd:
                    health_status = "chd_required"
                    missing_roms_str = required_chd
                elif required_parent and required_parent not in available_rom_names:
                    health_status = "parent_required"
                    missing_roms_str = required_parent
                elif required_bios and not bios_ready:
                    health_status = "bios_required"
                    missing_roms_str = required_bios
                elif is_arcade and curr_file_path.endswith(".zip"):
                    dat_db_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arcade_dat.db")
                    if os.path.isfile(dat_db_p):
                        try:
                            with zipfile.ZipFile(curr_file_path, "r") as z_chk:
                                file_crcs = {f"{z_chk.getinfo(n).CRC:08x}".lower() for n in z_chk.namelist() if not n.startswith(".") and z_chk.getinfo(n).file_size > 0}
                            
                            c_dat = sqlite3.connect(dat_db_p, timeout=5)
                            cur_dat = c_dat.cursor()
                            target_sys = "MAME2003Plus" if r_core == "mame2003" else "FBNeo"
                            cur_dat.execute("SELECT r.rom_name, r.crc32 FROM games g JOIN roms r ON g.id = r.game_id WHERE g.name = ? AND g.system_name = ?", (gcode or stem, target_sys))
                            expected = cur_dat.fetchall()
                            if not expected and target_sys == "FBNeo":
                                cur_dat.execute("SELECT r.rom_name, r.crc32 FROM games g JOIN roms r ON g.id = r.game_id WHERE g.name = ? AND g.system_name = 'MAME2003Plus'", (gcode or stem,))
                                expected = cur_dat.fetchall()
                            c_dat.close()

                            if expected:
                                m_roms = [r[0] for r in expected if r[1] not in file_crcs]
                                if m_roms:
                                    health_status = "incomplete"
                                    missing_roms_str = json.dumps(m_roms[:6], ensure_ascii=False)
                            else:
                                health_status = "bad_dump_or_unknown"
                                missing_roms_str = "DAT 미일치 또는 미지원/손상 의심 롬셋"
                        except Exception:
                            pass

                existing_cover_file = self._resolve_existing_cover(
                    gid,
                    info["filename"],
                    rom_info.get("core") or rom_info.get("platform"),
                    current_cover_path=(existing_games.get(gid) or {}).get("cover_path") or "",
                    update_db=False,
                )

                try:
                    if gid not in existing_games:
                        self._db_execute(
                            """INSERT OR REPLACE INTO games (id, filename, file_path, title, game_code, maker_code, core, platform, size_bytes, mtime, added_at, cover_path, needed_bios, health_status, missing_roms, rom_crc32, rom_md5, rom_sha1, serial_code, normalized_title, source_system, metadata_source, metadata_confidence, region_tag, revision_tag, disc_number, content_flags)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                                existing_cover_file,
                                rom_info.get("needed_bios") or "",
                                health_status,
                                missing_roms_str,
                                identity_info.get("rom_crc32") or "",
                                identity_info.get("rom_md5") or "",
                                identity_info.get("rom_sha1") or "",
                                identity_info.get("serial_code") or "",
                                identity_info.get("normalized_title") or "",
                                identity_info.get("source_system") or "",
                                identity_info.get("metadata_source") or "",
                                identity_info.get("metadata_confidence") or 0,
                                identity_info.get("region_tag") or "",
                                identity_info.get("revision_tag") or "",
                                identity_info.get("disc_number") or 0,
                                identity_info.get("content_flags") or "",
                            ),
                        )
                        new_games_added.append(clean_title or info["filename"])
                        if not existing_cover_file:
                            covers_to_fetch.append((
                                gid,
                                rom_info.get("core") or rom_info.get("platform"),
                                info["filename"],
                                curr_file_path,
                                mapped_header or clean_title
                            ))
                    else:
                        self._db_execute(
                            "UPDATE games SET file_path = ?, size_bytes = ?, mtime = ?, core = ?, platform = ?, title = ?, game_code = ?, needed_bios = ?, health_status = ?, missing_roms = ?, rom_crc32 = ?, rom_md5 = ?, rom_sha1 = ?, serial_code = ?, normalized_title = ?, source_system = ?, metadata_source = COALESCE(NULLIF(metadata_source, ''), ?), metadata_confidence = CASE WHEN metadata_confidence IS NULL OR metadata_confidence = 0 THEN ? ELSE metadata_confidence END, region_tag = ?, revision_tag = ?, disc_number = ?, content_flags = ?, cover_path = COALESCE(cover_path, ?) WHERE id = ?",
                            (curr_file_path, info["size_bytes"], info["mtime"], rom_info["core"], rom_info["platform"], clean_title, rom_info["game_code"], rom_info.get("needed_bios") or "", health_status, missing_roms_str, identity_info.get("rom_crc32") or "", identity_info.get("rom_md5") or "", identity_info.get("rom_sha1") or "", identity_info.get("serial_code") or "", identity_info.get("normalized_title") or "", identity_info.get("source_system") or "", identity_info.get("metadata_source") or "", identity_info.get("metadata_confidence") or 0, identity_info.get("region_tag") or "", identity_info.get("revision_tag") or "", identity_info.get("disc_number") or 0, identity_info.get("content_flags") or "", existing_cover_file, gid),
                        )
                        existing_entry = existing_games.get(gid)
                        current_cover = existing_entry.get("cover_path") if existing_entry else None
                        if not current_cover or not os.path.exists(current_cover):
                            if existing_cover_file:
                                self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (existing_cover_file, gid))
                            elif not new_only:
                                covers_to_fetch.append((
                                    gid,
                                    rom_info.get("core") or rom_info.get("platform"),
                                    info["filename"],
                                    curr_file_path,
                                    mapped_header or clean_title
                                ))
                except Exception as dbe:
                    logger.warning(f"[{SELF_ID}] Game DB update error ({gid}): {dbe}")

            # 삭제된 게임 정리
            for gid in existing_games:
                if gid not in found_files:
                    try:
                        self._db_execute("DELETE FROM games WHERE id = ?", (gid,))
                        self._db_execute("DELETE FROM user_game_data WHERE game_id = ?", (gid,))
                        deleted_count += 1
                    except Exception:
                        pass

        # 누락된 커버 이미지를 전역 백그라운드 다운로드 큐에 추가
        if covers_to_fetch:
            _enqueue_cover_downloads(self, covers_to_fetch)

        _update_scan_progress(
            current=total_files,
            total=total_files,
            current_file="스캔 완료",
            status="completed",
            is_running=False,
        )

        return {
            "success": True,
            "new_count": len(new_games_added),
            "new_games": new_games_added,
            "deleted_count": deleted_count,
            "total_files": total_files,
        }

    def _get_setting(self, key, default=""):
        """설정값 조회: 게임북 전용 로컬 SQLite DB(settings 테이블)에서 조회합니다."""
        try:
            rows = self._db_query("SELECT value FROM settings WHERE key = ?", (key,))
        except sqlite3.OperationalError as e:
            if "no such table: settings" in str(e).lower():
                logger.warning(f"[{SELF_ID}] settings table missing during _get_setting({key}); returning default")
                return default
            raise
        if rows:
            return rows[0]["value"]
        return default

    def _set_setting(self, key, value):
        """설정값 저장: 게임북 전용 로컬 SQLite DB(settings 테이블)에 저장합니다."""
        self._db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

    def _managed_storage_roots(self):
        """파일 이동/삭제가 허용되는 Game Books 관리 저장소 루트 목록."""
        roots = [
            self._get_data_dir(), self._get_roms_dir(), self._get_bios_dir(), self._get_covers_dir(),
            self._get_emulatorjs_root(),
            self._get_setting("EXTRA_ROMS_PATH", "").strip(),
            self._get_setting("COVERS_PATH", "").strip(),
            self._get_setting("BIOS_PATH", "").strip(),
            "/mnt/gdrive/emulatorjs",
        ]
        return [r for r in roots if r]

    def _is_managed_storage_path(self, path):
        return bool(path) and _path_within_any(path, self._managed_storage_roots())

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
                    "gba_preflight_check": (f"{ROUTE_BASE}/preflight", ["POST"]),
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

        if _get_current_user_id() <= 0:
            abort(401, "Authentication required")

        rows = self._db_query("SELECT file_path, filename FROM games WHERE id = ?", (game_id,))
        if not rows or not os.path.exists(rows[0]["file_path"]):
            abort(404, "ROM file not found")

        root_file_path = rows[0]["file_path"]
        requested_filename = os.path.basename((filename or rows[0]["filename"] or "").strip())
        served_path = root_file_path
        root_filename = os.path.basename(rows[0]["filename"] or root_file_path)
        expected_bundle_filename = os.path.splitext(root_filename)[0] + ".zip"

        # 멀티파일 ROM (디스크 번들: .cue + .bin, .gdi 등) 기본 다운로드/실행 요청 시 ZIP 번들 생성 서빙
        # requested_filename이 특정 sidecar 파일을 명시적으로 요구한 경우가 아니면 bundle zip으로 서빙
        is_direct_sidecar_request = False
        if requested_filename and requested_filename not in (root_filename, expected_bundle_filename):
            for candidate_path in _collect_disk_bundle_paths(root_file_path):
                if os.path.basename(candidate_path) == requested_filename:
                    served_path = candidate_path
                    is_direct_sidecar_request = True
                    break
            else:
                abort(404, "ROM sidecar file not found")

        file_path = served_path
        actual_filename = os.path.basename(file_path)
        ext = os.path.splitext(file_path)[1].lower()

        # 멀티파일 디스크 번들 (.cue, .gdi 등 또는 sidecar가 2개 이상 존재하는 경우):
        # 멀티디스크 게임은 백엔드에서 zip + .m3u 아티팩트를 조립하여 EJS_gameUrl 단일 진입점으로 서빙
        if not is_direct_sidecar_request and ext in (".cue", ".gdi", ".bin", ".iso", ".img"):
            bundle_files = [p for p in _collect_disk_bundle_paths(root_file_path) if os.path.isfile(p)]
            if len(bundle_files) > 1:
                try:
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                        has_m3u = any(p.lower().endswith(".m3u") for p in bundle_files)
                        for b_path in bundle_files:
                            b_name = os.path.basename(b_path)
                            zout.write(b_path, b_name)

                        # .m3u가 번들에 없고 .cue가 포함되어 있는 경우 플레이리스트 자동 추가
                        if not has_m3u:
                            clean_stem = os.path.splitext(os.path.basename(root_file_path))[0]
                            m3u_bytes = _generate_m3u_content_for_paths(bundle_files)
                            if m3u_bytes:
                                zout.writestr(f"{clean_stem}.m3u", m3u_bytes)

                    zip_bytes = zip_buffer.getvalue()
                    clean_stem = os.path.splitext(os.path.basename(root_file_path))[0]
                    zip_filename = f"{clean_stem}.zip"

                    ascii_fallback = re.sub(r"[^\x20-\x7E]", "_", zip_filename)
                    encoded_filename = urllib.parse.quote(zip_filename)
                    content_disposition = f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'

                    resp = Response(zip_bytes, 200, mimetype="application/zip")
                    resp.headers["Content-Length"] = str(len(zip_bytes))
                    resp.headers["Content-Disposition"] = content_disposition
                    resp.headers["Cache-Control"] = "public, max-age=86400"
                    return resp
                except Exception as b_err:
                    logger.error(f"[{SELF_ID}] Multi-file disk bundle zip creation error: {b_err}")

        # .7z 압축 롬 파일인 경우: py7zr을 이용해 내부 롬 파일을 표준 .zip 형식으로 메모리 변환하여 서빙
        if ext == ".7z":
            try:
                import py7zr
                if py7zr.is_7zfile(file_path):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        _safe_7z_extract(file_path, tmpdir)
                        extracted_files = []

                        # 표준 ZIP 아카이브 메모리 생성
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                            for root, _dirs, files in os.walk(tmpdir):
                                for fname in files:
                                    full_ef = os.path.join(root, fname)
                                    arc_ef = os.path.relpath(full_ef, tmpdir).replace(os.sep, "/")
                                    if arc_ef.startswith("."):
                                        continue
                                    extracted_files.append(arc_ef)
                                    zout.write(full_ef, arc_ef)

                            # 네오지오(Neo-Geo) 기종 롬셋인 경우: bios/neogeo.zip 바이오스 칩셋 파일들을 ZIP 내부에 자동 병합하여
                            # FBNeo 웹어셈블리 코어의 "missing files for THIS VERSION of FBNeo (Verify romsets: neogeo)" 방지
                            rows_game = self._db_query("SELECT platform, needed_bios FROM games WHERE id = ?", (game_id,))
                            if rows_game and (rows_game[0].get("platform") == "Neo-Geo" or rows_game[0].get("needed_bios") == "neogeo.zip"):
                                bios_p = os.path.join(self._get_bios_dir(), "neogeo.zip")
                                if os.path.isfile(bios_p):
                                    try:
                                        with zipfile.ZipFile(bios_p, "r") as zb:
                                            existing_names = set(extracted_files)
                                            for b_info in zb.infolist():
                                                if b_info.filename not in existing_names and not b_info.filename.startswith("."):
                                                    zout.writestr(b_info.filename, zb.read(b_info.filename))
                                    except Exception as b_ex:
                                        logger.debug(f"[{SELF_ID}] Bios merge error: {b_ex}")

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
                if start >= file_size or end < start:
                    resp = Response(b"", 416, mimetype="application/octet-stream")
                    resp.headers["Content-Range"] = f"bytes */{file_size}"
                    return resp
                end = min(end, file_size - 1)
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
        from flask import Response, abort, request

        if _get_current_user_id() <= 0:
            abort(401, "Authentication required")

        safe_name = os.path.basename(filename)
        game_id = request.args.get("game_id", "").strip()
        target_path = self._find_runtime_bios_path(safe_name, game_id=game_id if game_id else None)

        if not target_path or not os.path.isfile(target_path):
            return Response("BIOS file not found", 404)

        file_size = os.path.getsize(target_path)
        actual_fname = os.path.basename(target_path)
        ascii_fallback = re.sub(r"[^\x20-\x7E]", "_", actual_fname)
        encoded_filename = urllib.parse.quote(actual_fname)
        content_disposition = f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'

        range_header = request.headers.get("Range", None)
        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                if start >= file_size or end < start:
                    resp = Response(b"", 416, mimetype="application/octet-stream")
                    resp.headers["Content-Range"] = f"bytes */{file_size}"
                    return resp
                end = min(end, file_size - 1)
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
        if user_id <= 0:
            return jsonify({"success": False, "error": "로그인이 필요합니다."}), 401
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
        if user_id <= 0:
            return jsonify({"success": False, "error": "로그인이 필요합니다."}), 401
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

        if _get_current_user_id() <= 0:
            abort(401, "Authentication required")

        rows = self._db_query(
            "SELECT cover_path, file_path, filename, core, platform, normalized_title, title FROM games WHERE id = ?",
            (game_id,),
        )
        if not rows:
            abort(404, "Cover image not found")
        game = rows[0]
        cover_path = self._resolve_existing_cover(
            game_id,
            game.get("filename") or os.path.basename(game.get("file_path") or ""),
            game.get("core") or game.get("platform") or "",
            current_cover_path=game.get("cover_path") or "",
            update_db=True,
        )
        if not cover_path or not os.path.exists(cover_path):
            abort(404, "Cover image not found")

        ext = os.path.splitext(str(cover_path))[1].lower().replace(".", "")
        mime = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/webp"

        with open(str(cover_path), "rb") as f:
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

        # ROM/BIOS/커버는 모두 공용 라이브러리를 변경하므로 관리자만 허용합니다.
        if upload_type in ("rom", "bios", "cover") and not _is_current_user_admin():
            return jsonify({"success": False, "error": "관리자만 ROM, 바이오스 및 공용 커버를 업로드할 수 있습니다."}), 403

        raw_filename = file.filename
        safe_filename = re.sub(r"[^\w\.\-\(\) ]", "_", raw_filename)
        ext = os.path.splitext(safe_filename)[1].lower()
        base_n, ext_n = os.path.splitext(safe_filename)

        allowed_rom_exts = set(SUPPORTED_SYSTEMS.keys()) | {".zip", ".7z", ".bin", ".rom", ".cue", ".gdi", ".iso", ".img", ".chd"}
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

            dest_path = os.path.join(target_sub_dir, safe_filename)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(target_sub_dir, f"{base_n}_{counter}{ext_n}")
                counter += 1

            # 모든 .7z 압축 롬 업로드 시: 브라우저 WebAssembly 에뮬레이터 호환성을 위해 표준 .zip으로 영구 자동 변환
            if ext == ".7z":
                try:
                    import py7zr
                    zip_safe_filename = f"{base_n}.zip"
                    dest_zip_path = os.path.join(target_sub_dir, zip_safe_filename)
                    z_counter = 1
                    while os.path.exists(dest_zip_path):
                        dest_zip_path = os.path.join(target_sub_dir, f"{base_n}_{z_counter}.zip")
                        z_counter += 1

                    work_zip_path = dest_zip_path + ".part"
                    with tempfile.TemporaryDirectory() as tmpdir:
                        _safe_7z_extract(temp_dest, tmpdir)
                        extracted_files = []
                        with zipfile.ZipFile(work_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                            for root, _dirs, files in os.walk(tmpdir):
                                for ef in files:
                                    full_ef = os.path.join(root, ef)
                                    arc_ef = os.path.relpath(full_ef, tmpdir).replace(os.sep, "/")
                                    if arc_ef.startswith("."):
                                        continue
                                    extracted_files.append(arc_ef)
                                    zout.write(full_ef, arc_ef)
                                if rom_info.get("platform") == "Neo-Geo" or rom_info.get("needed_bios") == "neogeo.zip":
                                    bios_p = os.path.join(self._get_bios_dir(), "neogeo.zip")
                                    if os.path.exists(bios_p):
                                        with zipfile.ZipFile(bios_p, "r") as zb:
                                            existing = set(extracted_files)
                                            for b_info in zb.infolist():
                                                if b_info.filename not in existing and not b_info.filename.startswith("."):
                                                    zout.writestr(b_info.filename, zb.read(b_info.filename))
                    if not _validate_zip_file(work_zip_path):
                        raise ValueError("converted ZIP validation failed")
                    os.replace(work_zip_path, dest_zip_path)
                    if os.path.exists(temp_dest):
                        os.remove(temp_dest)
                    safe_filename = os.path.basename(dest_zip_path)
                except Exception as conv_ex:
                    logger.error(f"[{SELF_ID}] Upload 7z convert error: {conv_ex}")
                    shutil.move(temp_dest, dest_path)
            else:
                shutil.move(temp_dest, dest_path)
                bundle_result = _move_disk_bundle(dest_path, target_sub_dir)
                dest_path = bundle_result.get("primary_path") or dest_path
                safe_filename = os.path.basename(dest_path)

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

    def _route_preflight_check(self):
        """업로드 전 클라이언트 헤더 기반 초고속 Pre-flight 검증 (/preflight)"""
        from flask import jsonify, request

        if not _is_current_user_admin():
            return jsonify({"success": False, "error": "관리자만 ROM 업로드 사전 검사를 사용할 수 있습니다."}), 403

        payload = request.get_json(silent=True) or {}
        filename = str(payload.get("filename", "")).strip()
        filesize = int(payload.get("filesize", 0))
        head_hex = str(payload.get("head_hex", "")).strip().lower()
        internal_names = [str(n).lower() for n in payload.get("internal_names", [])]

        safe_filename = re.sub(r"[^\w\.\-\(\) ]", "_", filename)
        stem = os.path.splitext(safe_filename)[0].lower()
        ext = os.path.splitext(safe_filename)[1].lower()

        # 1. 바이오스 여부 사전 판정
        is_bios = _is_bios_file(safe_filename)
        if any(any(k in n for k in ("bios", "boardrom", "bootrom", "firmware", "coh-1000", "coh-1001")) for n in internal_names):
            is_bios = True

        if is_bios:
            return jsonify({
                "valid": True,
                "is_bios": True,
                "platform": "BIOS",
                "message": f"'{safe_filename}' 파일은 시스템 바이오스(BIOS)로 자동 분류되어 등록됩니다.",
            })

        # 2. 콘솔/아케이드 기종 및 필요 바이오스 사전 판정
        detected_platform = "Unknown"
        needed_bios = ""
        is_split = False
        parent_hint = ""

        # NES 헤더 (4E45531A)
        if head_hex.startswith("4e45531a") or ext in (".nes", ".fds"):
            detected_platform = "FDS" if ext == ".fds" else "NES"
            if ext == ".fds":
                needed_bios = "disksys.rom"
        # GBA 헤더
        elif ext == ".gba" or (len(head_hex) >= 16 and head_hex[8:16] == "200000ea"):
            detected_platform = "GBA"
        # N64 매직넘버 (80371240 / 37804012 / 40123780)
        elif ext in (".z64", ".n64", ".v64") or head_hex.startswith(("80371240", "37804012", "40123780")):
            detected_platform = "N64"
        # SNES
        elif ext in (".sfc", ".smc"):
            detected_platform = "SNES"
        # MD / Genesis
        elif ext in (".md", ".gen", ".smd"):
            detected_platform = "Genesis"
        # PS1 / Saturn / Dreamcast / PCE-CD
        elif ext in (".iso", ".chd", ".cue", ".pbp", ".gdi"):
            if ext == ".gdi":
                detected_platform = "Dreamcast"
            elif ext == ".cue":
                detected_platform = "PS1/Saturn (serial 확인 권장)"
            elif ext == ".chd" and any(tok in stem for tok in ("pce", "tg16", "supercd", "pcengine")):
                detected_platform = "PCECD"
                needed_bios = "syscard3.pce"
            else:
                detected_platform = "PS1"
                needed_bios = "scph5501.bin"
        elif ext == ".zip":
            # 아케이드 롬셋 DAT DB 사전 정밀 분석
            dat_match = _query_arcade_dat(stem)
            if dat_match:
                detected_platform = "Neo-Geo" if (dat_match.get("romof") == "neogeo" or stem in KNOWN_NEOGEO_STEMS) else "Arcade"
                if dat_match.get("romof"):
                    romof_bios = dat_match["romof"].lower()
                    if not romof_bios.endswith(".zip") and not romof_bios.endswith(".bin"):
                        romof_bios += ".zip"
                    needed_bios = romof_bios
                if dat_match.get("cloneof"):
                    parent_hint = dat_match["cloneof"]
            elif stem in KNOWN_ARCADE_TITLES or stem in KNOWN_NEOGEO_STEMS:
                detected_platform = "Neo-Geo" if stem in KNOWN_NEOGEO_STEMS else "Arcade"
                if stem in KNOWN_NEOGEO_STEMS or any(stem.startswith(k) for k in ("mslug", "kof", "samsho", "fatfur", "garou", "aof", "lastblad")):
                    needed_bios = "neogeo.zip"
                elif any(stem.startswith(k) for k in ("olds", "kov", "orlegend", "dmnfrnt")):
                    needed_bios = "pgm.zip"
                elif any(stem.startswith(k) for k in ("bldyror", "brvblade", "sfex", "rvschool", "starglad", "strider2", "techromn")):
                    needed_bios = "acpsx.zip"
            elif internal_names:
                # 내부 파일 개수 및 용량 기반 스플릿/완본 휴리스틱
                if filesize < 200 * 1024 and len(internal_names) <= 3:
                    is_split = True
                    parent_hint = stem[:-1] if stem[-1] in "juka" and len(stem) > 3 else stem

        return jsonify({
            "valid": True,
            "is_bios": False,
            "platform": detected_platform,
            "needed_bios": needed_bios,
            "is_split": is_split,
            "parent_hint": parent_hint,
            "message": "Pre-flight check passed",
        })

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
        is_admin = _is_current_user_admin()

        # 공용 라이브러리/서버 설정을 변경하거나 내부 저장소 정보를 노출하는 액션은
        # UI 표시 여부와 관계없이 서버에서 관리자 권한을 강제합니다.
        if user_id <= 0:
            return {"success": False, "error": "로그인이 필요합니다."}

        admin_actions = {
            "check_game",
            "get_cover_migration_candidates", "get_bios_migration_candidates",
            "migrate_bios_batch", "migrate_cover_batch",
            "scan_new_roms", "scan_roms", "full_scan", "health_check",
            "fetch_missing_covers", "delete_game", "update_title", "save_settings", "search_artwork", "set_artwork",
        }
        if action in admin_actions and not is_admin:
            return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}

        try:
            if action == "list_games":
                # DB에 게임 데이터가 한 건도 없을 때만 최초 1회 자동 초기 스캔
                game_count_row = self._db_query("SELECT COUNT(*) AS cnt FROM games")
                if not game_count_row or game_count_row[0]["cnt"] == 0:
                    self._scan_roms()

                games = self._db_query(
                    """SELECT g.id, g.filename, g.file_path, g.title, g.game_code, g.maker_code,
                              g.size_bytes, g.added_at, g.cover_path, g.core, g.platform, g.needed_bios,
                              COALESCE(g.health_status, 'pass') AS health_status,
                              COALESCE(g.missing_roms, '') AS missing_roms,
                              COALESCE(g.rom_crc32, '') AS rom_crc32,
                              COALESCE(g.rom_md5, '') AS rom_md5,
                              COALESCE(g.rom_sha1, '') AS rom_sha1,
                              COALESCE(g.serial_code, '') AS serial_code,
                              COALESCE(g.normalized_title, '') AS normalized_title,
                              COALESCE(g.source_system, '') AS source_system,
                              COALESCE(g.metadata_source, '') AS metadata_source,
                              COALESCE(g.metadata_confidence, 0) AS metadata_confidence,
                              COALESCE(g.canonical_title, '') AS canonical_title,
                              COALESCE(g.alt_titles, '') AS alt_titles,
                              COALESCE(g.region, '') AS region,
                              COALESCE(g.genre, '') AS genre,
                              COALESCE(g.developer, '') AS developer,
                              COALESCE(g.publisher, '') AS publisher,
                              COALESCE(g.release_year, '') AS release_year,
                              COALESCE(g.players, 0) AS players,
                              COALESCE(g.description, '') AS description,
                              COALESCE(g.region_tag, '') AS region_tag,
                              COALESCE(g.revision_tag, '') AS revision_tag,
                              COALESCE(g.disc_number, 0) AS disc_number,
                              COALESCE(g.content_flags, '') AS content_flags,
                              COALESCE(u.is_favorite, 0) AS is_favorite,
                              u.last_played_at,
                              COALESCE(u.play_count, 0) AS play_count
                       FROM games g
                       LEFT JOIN user_game_data u ON g.id = u.game_id AND u.user_id = ?
                       ORDER BY is_favorite DESC, u.last_played_at DESC, added_at DESC""",
                    (user_id,),
                )

                user_saves_dir = self._get_user_saves_dir(user_id)
                existing_saves = set()
                if os.path.exists(user_saves_dir):
                    try:
                        for sf in os.listdir(user_saves_dir):
                            if not sf.startswith("."):
                                full_sf = os.path.join(user_saves_dir, sf)
                                try:
                                    if os.path.getsize(full_sf) > 0:
                                        existing_saves.add(sf)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                visible_games = []
                # 카드에는 서버 절대경로 대신 활성 라이브러리 루트 기준 상대경로만 노출한다.
                path_roots = []
                for candidate in (
                    self._get_setting("EXTRA_ROMS_PATH", "").strip(),
                    self._get_roms_dir(),
                    self._get_emulatorjs_root(),
                ):
                    if candidate:
                        root_abs = os.path.realpath(os.path.abspath(candidate))
                        if root_abs not in path_roots:
                            path_roots.append(root_abs)
                # 중첩된 경로라면 가장 구체적인(긴) 루트를 우선한다.
                path_roots.sort(key=len, reverse=True)

                for g in games:
                    gid = g["id"]
                    file_path = g.get("file_path") or ""
                    current_cover = g.get("cover_path") or ""
                    if not current_cover or not os.path.exists(current_cover):
                        repaired_cover = self._resolve_existing_cover(
                            gid,
                            g.get("filename") or "",
                            g.get("core") or g.get("platform") or "",
                            current_cover_path=current_cover,
                            update_db=True,
                        )
                        if repaired_cover:
                            g["cover_path"] = repaired_cover
                    g["relative_path"] = g.get("filename") or ""
                    if file_path:
                        file_abs = os.path.realpath(os.path.abspath(file_path))
                        for root_abs in path_roots:
                            try:
                                if os.path.commonpath([file_abs, root_abs]) == root_abs:
                                    g["relative_path"] = os.path.relpath(file_abs, root_abs).replace(os.sep, "/")
                                    break
                            except (ValueError, OSError):
                                continue
                    has_sav = f"{gid}.sav" in existing_saves
                    has_state = f"{gid}.state" in existing_saves or f"{gid}_slot1.state" in existing_saves

                    g["has_save"] = 1 if (has_sav or has_state) else 0
                    g["has_state"] = 1 if has_state else 0
                    url_fname = g["filename"]
                    if url_fname.lower().endswith(".7z"):
                        url_fname = os.path.splitext(url_fname)[0] + ".zip"
                    elif os.path.splitext(url_fname)[1].lower() in (".cue", ".gdi"):
                        url_fname = os.path.splitext(url_fname)[0] + ".zip"
                    g["rom_url"] = f"{ROUTE_BASE}/rom/{gid}/{urllib.parse.quote(url_fname)}"
                    g["disk_file_urls"] = {}
                    g["save_url"] = f"{ROUTE_BASE}/save/{gid}"
                    g["state_url"] = f"{ROUTE_BASE}/state/{gid}"
                    g["cover_url"] = f"{ROUTE_BASE}/cover/{gid}"
                    g["has_needed_bios"] = 1
                    if not is_admin:
                        g["cover_path"] = bool(g.get("cover_path"))
                    # 관리자 여부와 관계없이 브라우저에는 서버 절대경로를 보내지 않는다.
                    g.pop("file_path", None)
                    visible_games.append(g)

                return {
                    "success": True,
                    "games": visible_games,
                    "total_count": len(visible_games),
                    "available_bios": [],
                    "user_id": user_id,
                    "is_admin": is_admin,
                    "config": {
                        "cloud_save_enabled": str(self._get_setting("CLOUD_SAVE_ENABLED", "1")).lower() in ("1", "true", "yes", "on"),
                        "auto_save_interval_sec": int(self._get_setting("AUTO_SAVE_INTERVAL_SEC", "60")),
                        # 서버 경로와 외부 서비스 자격증명은 관리자 화면에만 전달합니다.
                        "emulatorjs_root": str(self._get_setting("EMULATORJS_ROOT", "") or self._get_emulatorjs_root() or "").strip() if is_admin else "",
                        "extra_roms_path": str(self._get_setting("EXTRA_ROMS_PATH", "") or "").strip() if is_admin else "",
                        "covers_path": str(self._get_setting("COVERS_PATH", "") or "").strip() if is_admin else "",
                        "bios_path": str(self._get_setting("BIOS_PATH", "") or "").strip() if is_admin else "",
                        "ss_devid": str(self._get_setting("SS_DEVID", "") or "").strip() if is_admin else "",
                        "ss_devpassword": str(self._get_setting("SS_DEVPASSWORD", "") or "").strip() if is_admin else "",
                        "ss_user": str(self._get_setting("SS_USER", "") or "").strip() if is_admin else "",
                        "ss_password": str(self._get_setting("SS_PASSWORD", "") or "").strip() if is_admin else "",
                        "igdb_client_id": str(self._get_setting("IGDB_CLIENT_ID", "") or "").strip() if is_admin else "",
                        "igdb_client_secret": str(self._get_setting("IGDB_CLIENT_SECRET", "") or "").strip() if is_admin else "",
                        "max_content_length_mb": int(os.environ.get("MAX_CONTENT_LENGTH_MB", "100") or 100),
                        "max_upload_bytes": int(os.environ.get("MAX_CONTENT_LENGTH_MB", "100") or 100) * 1024 * 1024,
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

                if not self._is_managed_storage_path(target_dir):
                    return {"success": False, "error": "허용되지 않은 대상 경로입니다."}
                os.makedirs(target_dir, exist_ok=True)
                items = json_data.get("items", [])
                moved_count = 0

                for it in items:
                    src_p = it.get("path")
                    fname = os.path.basename(it.get("name") or (os.path.basename(src_p) if src_p else ""))

                    if src_p and os.path.exists(src_p) and self._is_managed_storage_path(src_p):
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

                if not self._is_managed_storage_path(target_dir):
                    return {"success": False, "error": "허용되지 않은 대상 경로입니다."}
                os.makedirs(target_dir, exist_ok=True)
                items = json_data.get("items", [])
                moved_count = 0

                for it in items:
                    src_p = it.get("path")
                    fname = os.path.basename(it.get("name") or (os.path.basename(src_p) if src_p else ""))
                    game_id = it.get("game_id")

                    if src_p and os.path.exists(src_p) and self._is_managed_storage_path(src_p):
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
                res = self._scan_roms(new_only=True)
                new_cnt = res.get("new_count", 0) if isinstance(res, dict) else 0
                new_sample = res.get("new_games", []) if isinstance(res, dict) else []
                msg = f"신규 ROM {new_cnt}개가 성공적으로 등록되었습니다." if new_cnt > 0 else "새로 추가된 ROM 파일이 없습니다."
                return {"success": True, "message": msg, "stats": res}

            elif action == "scan_progress":
                with _SCAN_PROGRESS_LOCK:
                    prog = dict(_SCAN_PROGRESS)
                return {
                    "success": True,
                    "progress": prog,
                    "cover_queue": _get_cover_queue_status(),
                }


            elif action == "cover_queue_status":
                return {
                    "success": True,
                    "cover_queue": _get_cover_queue_status(),
                }

            elif action == "fetch_missing_covers":
                # DB 경로가 비어 있거나 실제 파일이 사라진 게임 모두 검사한다.
                # 경로 기반 game_id 변경으로 끊긴 기존 커버는 먼저 재연결하고,
                # 로컬 커버를 찾지 못한 게임만 외부 다운로드 큐에 투입한다.
                rows = self._db_query("SELECT id, core, platform, filename, file_path, title, cover_path FROM games")
                missing_items = []
                repaired_count = 0
                for r in rows:
                    gid = r["id"]
                    core_p = r.get("core") or r.get("platform")
                    fname = r.get("filename") or ""
                    fpath = r.get("file_path") or ""
                    raw_t = r.get("title") or fname
                    current_cover = r.get("cover_path") or ""
                    resolved_cover = self._resolve_existing_cover(
                        gid,
                        fname,
                        core_p,
                        current_cover_path=current_cover,
                        update_db=True,
                    )
                    if resolved_cover:
                        if not current_cover or os.path.realpath(current_cover) != os.path.realpath(resolved_cover):
                            repaired_count += 1
                        continue
                    missing_items.append((gid, core_p, fname, fpath, raw_t))
                if missing_items:
                    _enqueue_cover_downloads(self, missing_items)
                return {
                    "success": True,
                    "repaired_count": repaired_count,
                    "enqueued_count": len(missing_items),
                    "cover_queue": _get_cover_queue_status(),
                }

            elif action == "full_scan":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}
                with _SCAN_PROGRESS_LOCK:
                    is_running = _SCAN_PROGRESS.get("is_running", False)
                if not is_running:
                    threading.Thread(target=self._scan_roms, kwargs={"force_full": True}, daemon=True).start()
                return {"success": True, "message": "모든 ROM 파일의 전체 재스캔이 시작되었습니다."}

            elif action == "health_check":
                if not _is_current_user_admin():
                    return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}
                
                dat_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arcade_dat.db")
                if not os.path.isfile(dat_db_path):
                    return {"success": False, "error": "DAT 데이터베이스가 존재하지 않습니다."}

                conn_dat = sqlite3.connect(dat_db_path, timeout=10)
                cur_dat = conn_dat.cursor()

                all_games = self._db_query("SELECT id, filename, file_path, core, platform, title, game_code, needed_bios, COALESCE(health_status, 'pass') AS health_status, COALESCE(missing_roms, '') AS missing_roms FROM games ORDER BY title ASC")
                
                pass_count = 0
                incomplete_list = []
                chd_list = []

                for g in all_games:
                    gid = g["id"]
                    fname = g["filename"] or ""
                    fpath = g["file_path"] or ""
                    core = (g["core"] or "").lower()
                    plat = g["platform"] or ""
                    title = g["title"] or fname
                    health_status = (g.get("health_status") or "pass").strip() or "pass"
                    missing_roms = g.get("missing_roms") or ""
                    needed_bios = _normalize_required_archive(g.get("needed_bios") or "")

                    if not os.path.exists(fpath):
                        continue

                    if health_status == "pass":
                        pass_count += 1
                        continue

                    if health_status == "chd_required":
                        chd_list.append({
                            "id": gid,
                            "title": title,
                            "filename": fname,
                            "core": core,
                            "platform": plat,
                            "metadata_source": g.get("metadata_source") or "",
                            "metadata_confidence": g.get("metadata_confidence") or 0,
                            "source_system": g.get("source_system") or "",
                            "reason": "대용량 CD-ROM/음원 디스크 이미지(.chd)가 필요한 기판 롬셋입니다."
                        })
                        continue

                    if health_status == "incomplete":
                        try:
                            parsed_missing = json.loads(missing_roms) if missing_roms else []
                            missing_samples = parsed_missing[:4] if isinstance(parsed_missing, list) else []
                        except Exception:
                            missing_samples = []
                        reason = f"필수 칩셋 누락 ({', '.join(missing_samples[:3])}...)" if missing_samples else "필수 칩셋 누락이 있는 불완전 롬셋입니다."
                    elif health_status == "bios_required":
                        reason = f"필수 BIOS/시스템 파일 {needed_bios or missing_roms or '알 수 없음'} 누락"
                        missing_samples = []
                    elif health_status == "parent_required":
                        reason = f"필수 부모 롬 {missing_roms or '알 수 없음'} 누락"
                        missing_samples = []
                    elif health_status == "bad_dump_or_unknown":
                        reason = missing_roms or "DAT 미일치 또는 미지원/손상 의심 롬셋"
                        missing_samples = []
                    else:
                        pass_count += 1
                        continue

                    incomplete_list.append({
                        "id": gid,
                        "title": title,
                        "filename": fname,
                        "core": core,
                        "platform": plat,
                        "health_status": health_status,
                        "missing_samples": missing_samples,
                        "metadata_source": g.get("metadata_source") or "",
                        "metadata_confidence": g.get("metadata_confidence") or 0,
                        "source_system": g.get("source_system") or "",
                        "reason": reason,
                    })

                conn_dat.close()

                return {
                    "success": True,
                    "summary": {
                        "total": len(all_games),
                        "pass": pass_count,
                        "incomplete": len(incomplete_list),
                        "chd": len(chd_list)
                    },
                    "incomplete_list": incomplete_list,
                    "chd_list": chd_list
                }

            elif action == "scan_roms":
                res = self._scan_roms()
                return {"success": True, "message": "ROM 디스크 스캔 및 DB 동기화가 완료되었습니다.", "stats": res}

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
                            if self._is_managed_storage_path(rows[0]["file_path"]):
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
                emulatorjs_root = str(_get_val("emulatorjs_root", "")).strip()
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

                if emulatorjs_root:
                    self._set_setting("EMULATORJS_ROOT", emulatorjs_root)
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


# 플러그인 로드 완료 (코어 표준 라우트 맵 확장)
