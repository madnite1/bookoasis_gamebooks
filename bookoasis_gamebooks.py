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
_LIBRARY_SYNC_LOCK = threading.Lock()

_HEALTH_PROGRESS = {
    "is_running": False,
    "current": 0,
    "total": 0,
    "current_file": "",
    "status": "idle",
    "changed": 0,
    "cached": 0,
    "failed": 0,
    "updated_at": 0,
}
_HEALTH_PROGRESS_LOCK = threading.Lock()

_COVER_VARIANT_PROGRESS = {
    "is_running": False, "current": 0, "total": 0, "completed": 0, "failed": 0,
    "current_title": "", "status": "idle", "updated_at": 0,
}
_COVER_VARIANT_PROGRESS_LOCK = threading.Lock()

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


def _update_health_progress(current=None, total=None, current_file=None, status=None, is_running=None, changed=None, cached=None, failed=None):
    with _HEALTH_PROGRESS_LOCK:
        if current is not None:
            _HEALTH_PROGRESS["current"] = current
        if total is not None:
            _HEALTH_PROGRESS["total"] = total
        if current_file is not None:
            _HEALTH_PROGRESS["current_file"] = current_file
        if status is not None:
            _HEALTH_PROGRESS["status"] = status
        if is_running is not None:
            _HEALTH_PROGRESS["is_running"] = is_running
        if changed is not None:
            _HEALTH_PROGRESS["changed"] = changed
        if cached is not None:
            _HEALTH_PROGRESS["cached"] = cached
        if failed is not None:
            _HEALTH_PROGRESS["failed"] = failed
        _HEALTH_PROGRESS["updated_at"] = time.time()


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
        elif ext in (".bin", ".iso", ".img", ".pbp"):
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


def _parse_m3u_bundle(file_path):
    """vendored rom-analyzer의 안전한 M3U 파서를 사용해 실제 디스크 경로를 해석한다."""
    details = {
        "referenced_files": [],
        "resolved_files": [],
        "missing_files": [],
        "invalid_references": [],
        "error": "",
    }
    try:
        from rom_analyzer.disc.parsers import parse_m3u

        abs_path = os.path.abspath(str(file_path or ""))
        parsed = parse_m3u(abs_path, os.path.dirname(abs_path))
        details["referenced_files"] = list(parsed.referenced_files or [])
        details["resolved_files"] = [
            os.path.abspath(path)
            for path in (parsed.resolved_files or [])
            if path and os.path.isfile(path)
        ]
        details["missing_files"] = list(parsed.missing_files or [])
        details["invalid_references"] = list(parsed.invalid_references or [])
        details["error"] = str(parsed.error or "")
    except Exception as exc:
        details["error"] = f"M3U 파싱 실패: {type(exc).__name__}: {exc}"
    return details


def _filter_m3u_claimed_files(found_files):
    """M3U가 소유한 디스크 파일을 독립 게임 후보에서 제외한다."""
    claimed_disk_paths = set()
    for candidate in list((found_files or {}).values()):
        if os.path.splitext(candidate.get("filename") or "")[1].lower() != ".m3u":
            continue
        parsed = _parse_m3u_bundle(candidate.get("file_path") or "")
        for child_path in parsed.get("resolved_files") or []:
            if os.path.isfile(child_path):
                claimed_disk_paths.add(os.path.realpath(os.path.abspath(child_path)))

    if not claimed_disk_paths:
        return dict(found_files or {}), claimed_disk_paths

    filtered = {
        gid: info
        for gid, info in (found_files or {}).items()
        if os.path.splitext(info.get("filename") or "")[1].lower() == ".m3u"
        or os.path.realpath(os.path.abspath(info.get("file_path") or "")) not in claimed_disk_paths
    }
    return filtered, claimed_disk_paths


def _resolve_disk_sidecars(file_path):
    ext = os.path.splitext(str(file_path or ""))[1].lower()
    missing = []
    details = {"missing_files": [], "resolved_files": [], "serial_code": "", "disc_count": 1}
    if ext == ".m3u":
        parsed = _parse_m3u_bundle(file_path)
        details["resolved_files"] = parsed.get("resolved_files") or []
        details["missing_files"] = list(parsed.get("missing_files") or [])
        details["missing_files"].extend(
            f"잘못된 참조: {ref}" for ref in (parsed.get("invalid_references") or [])
        )
        if parsed.get("error"):
            details["missing_files"].append(parsed["error"])
        details["disc_count"] = max(1, len(parsed.get("referenced_files") or []))
        # CHD 압축 payload를 raw serial scanner로 읽지 않는다. 자식 CUE/ISO 등만 보조 검사한다.
        for resolved_path in details["resolved_files"]:
            if os.path.splitext(resolved_path)[1].lower() == ".chd":
                continue
            serial = _resolve_disk_sidecars(resolved_path).get("serial_code") or ""
            if serial:
                details["serial_code"] = serial
                break
        return details
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

    if ext == ".m3u":
        details = _resolve_disk_sidecars(file_path)
        for resolved_path in details.get("resolved_files") or []:
            _add(resolved_path)
            child_ext = os.path.splitext(resolved_path)[1].lower()
            if child_ext in (".cue", ".gdi"):
                for child_path in _collect_disk_bundle_paths(resolved_path):
                    _add(child_path)
        return bundle

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
    if ext not in (".cue", ".gdi", ".m3u") or not os.path.exists(file_path):
        return False

    try:
        if ext == ".m3u":
            raw_bytes = Path(file_path).read_bytes()
            text = None
            for encoding in ("utf-8-sig", "cp949", "cp1252", "latin-1"):
                try:
                    text = raw_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                return False
            lines = text.splitlines(keepends=True)
        else:
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
        elif ext == ".m3u":
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                quote = ""
                token = stripped
                if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
                    quote = token[0]
                    token = token[1:-1].strip()
                normalized = os.path.normpath(token.replace("\\", os.sep))
                if (
                    token
                    and "://" not in token
                    and not os.path.isabs(token)
                    and normalized not in ("", ".", "..")
                    and not normalized.startswith(".." + os.sep)
                ):
                    rel_name = os.path.basename(normalized)
                    new_token = f"{quote}{rel_name}{quote}" if quote else rel_name
                    prefix_len = len(line) - len(line.lstrip())
                    new_line = line[:prefix_len] + new_token
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
    destination_sources = {}
    for src_path in bundle_paths:
        dest_path = os.path.join(target_dir, os.path.basename(src_path))
        if os.path.abspath(dest_path) == src_path:
            continue
        previous_source = destination_sources.get(os.path.abspath(dest_path))
        if previous_source and previous_source != src_path:
            return {"moved": False, "primary_path": file_path, "move_map": {}, "conflict": dest_path}
        destination_sources[os.path.abspath(dest_path)] = src_path
        if os.path.exists(dest_path):
            return {"moved": False, "primary_path": file_path, "move_map": {}, "conflict": dest_path}
        move_map[src_path] = dest_path

    os.makedirs(target_dir, exist_ok=True)
    manifest_exts = {".cue", ".gdi", ".m3u"}
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


def _detect_rom_info_legacy(file_path):
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


def _analyze_rom_context(file_path):
    """rom-analyzer를 한 번 실행하고 원본 결과와 기존 Game Books dict를 함께 반환한다."""
    try:
        from rom_analysis_adapter import analyze_result, convert_result

        analysis_result = analyze_result(file_path)
        modern = convert_result(analysis_result)
        modern_core = str(modern.get("core") or "").strip()
        modern_platform = str(modern.get("platform") or "").strip()
        if modern_core and modern_platform:
            return analysis_result, modern
    except Exception as exc:
        logger.debug(
            f"[{SELF_ID}] rom-analyzer fallback ({os.path.basename(str(file_path))}): {exc}"
        )

    return None, _detect_rom_info_legacy(file_path)


def _detect_rom_info(file_path):
    """기존 호환 API: 분석 컨텍스트에서 Game Books dict만 반환한다."""
    return _analyze_rom_context(file_path)[1]


def _emulatorjs_unsupported_reason(rom_info):
    """rom-analyzer가 명시적으로 EmulatorJS 비호환 판정을 낸 경우 사용자용 사유를 반환한다."""
    if not isinstance(rom_info, dict) or rom_info.get("emulatorjs_supported") is not False:
        return ""
    warnings = rom_info.get("analysis_warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    reasons = [str(item).strip() for item in warnings if str(item).strip()]
    if reasons:
        return " / ".join(reasons[:3])
    return "현재 EmulatorJS Stable 코어에서 이 ROM/미디어 조합을 지원하지 않습니다."


def _mame_compatibility_health(rom_name):
    """MAME2003/Plus 호환성 스냅샷으로 빠르게 health 상태를 재판정한다."""
    driver = os.path.splitext(os.path.basename(str(rom_name or "")))[0].lower().strip()
    if not driver:
        return None
    try:
        from rom_analyzer.arcade.compatibility import ArcadeCompatibilityManager

        compatibility = ArcadeCompatibilityManager.get_for_rom(driver)
    except Exception as exc:
        logger.debug(f"[{SELF_ID}] MAME compatibility lookup failed ({driver}): {exc}")
        return None
    if not compatibility:
        return None

    ordered = []
    for core_id in ("mame2003", "mame2003_plus"):
        info = compatibility.get(core_id)
        if info:
            ordered.append((core_id, info))
    if not ordered:
        return None
    if any(info.supported for _core_id, info in ordered):
        return ("pass", "")

    details = ", ".join(f"{core_id}={info.driver_status}" for core_id, info in ordered)
    return ("unsupported", f"MAME2003 계열 게임 호환성 제한: {details}")


def _health_core_key(value):
    return str(value or "").strip().lower().replace("-", "").replace("_", "")


def _health_core_equivalent(db_core, db_platform, detected_core, detected_platform):
    """실행 코어의 호환 별칭은 같은 기종으로 취급하되 실제 기종 변경은 구분한다."""
    left_core = _health_core_key(db_core)
    right_core = _health_core_key(detected_core)
    left_platform = _health_core_key(db_platform)
    right_platform = _health_core_key(detected_platform)
    arcade_cores = {"arcade", "mame", "mame2003", "mame2003plus"}
    arcade_platforms = {"arcade", "neogeo"}
    if left_core in arcade_cores and right_core in arcade_cores and left_platform in arcade_platforms and right_platform in arcade_platforms:
        return True
    if left_core and right_core:
        return left_core == right_core
    return bool(left_platform and right_platform and left_platform == right_platform)


def _is_required_chd_available(file_path, required_chd):
    req = str(required_chd or "").strip().replace("\\", "/")
    if not req or not file_path:
        return False
    req = req.lstrip("/")
    if ".." in req.split("/"):
        return False
    base_dir = os.path.dirname(os.path.abspath(file_path))
    stem = os.path.splitext(os.path.basename(file_path))[0]
    candidates = [
        os.path.join(base_dir, req),
        os.path.join(base_dir, stem, req),
    ]
    if not os.path.splitext(req)[1]:
        candidates.extend([
            os.path.join(base_dir, f"{req}.chd"),
            os.path.join(base_dir, req, f"{req}.chd"),
            os.path.join(base_dir, stem, f"{req}.chd"),
        ])
    return any(os.path.exists(path) for path in candidates)


def _derive_health_status_from_analysis(rom_info, file_path, db_core="", db_platform="", db_game_code="", available_bios_names=None, bios_dir=""):
    """rom-analyzer의 명시적 근거만 사용해 Game Books 진단 상태를 계산한다.

    clone/CHD 파일명 추측 같은 legacy 휴리스틱은 사용하지 않는다.
    """
    if not isinstance(rom_info, dict) or rom_info.get("metadata_source") != "rom-analyzer":
        return "unverified", "rom-analyzer의 최신 판정 결과를 얻지 못했습니다."

    missing_files = [str(item).strip() for item in (rom_info.get("disk_missing_files") or []) if str(item).strip()]
    if missing_files:
        return "incomplete", json.dumps(missing_files[:8], ensure_ascii=False)

    identity = str(rom_info.get("identity_status") or "unknown").strip().lower()
    confidence = int(rom_info.get("metadata_confidence") or 0)
    core = str(rom_info.get("core") or "").strip()
    platform = str(rom_info.get("platform") or "").strip()
    conflicts = [str(item).strip() for item in (rom_info.get("analysis_conflicts") or []) if str(item).strip()]
    warnings = [str(item).strip() for item in (rom_info.get("analysis_warnings") or []) if str(item).strip()]
    ejs_reason = str(rom_info.get("emulatorjs_reason") or "").strip()
    explicit_compatibility_block = (
        rom_info.get("emulatorjs_supported") is False
        and any(
            token in (" ".join(warnings + [ejs_reason])).lower()
            for token in ("game not working", "호환성 제한", "실행 불가로 기록", "unemulated protection")
        )
    )

    # exact/strong 근거로 다른 기종이 확인된 경우 현재 실행 코어의 호환성 표보다 재분류가 우선한다.
    if core and platform and identity in ("exact", "strong") and (db_core or db_platform):
        if not _health_core_equivalent(db_core, db_platform, core, platform):
            return "reclassify_required", f"현재 등록: {db_platform or db_core} / 최신 판정: {platform or core} ({core})"

    # 현재 등록 코어가 MAME2003 계열이면 공식 게임별 호환성 스냅샷의 명시적 차단을
    # partial/ambiguous 식별보다 강한 실행 가능성 근거로 취급한다.
    if _health_core_key(db_core) in {"mame", "mame2003", "mame2003plus"}:
        compatibility_health = _mame_compatibility_health(
            rom_info.get("game_code") or db_game_code or os.path.basename(file_path or "")
        )
        if compatibility_health and compatibility_health[0] == "unsupported":
            return compatibility_health

    if not core or not platform or identity in ("unknown", "ambiguous"):
        reason = conflicts[0] if conflicts else f"rom-analyzer 판정이 {identity or 'unknown'} 상태입니다. 충분한 식별 근거가 없습니다."
        return "unverified", reason

    # analyzer 자체가 게임별 호환성 제한을 명시했다면 일반 신뢰도 등급보다 우선한다.
    if explicit_compatibility_block:
        return "unsupported", _emulatorjs_unsupported_reason(rom_info) or ejs_reason

    if identity == "partial":
        return "unverified", f"rom-analyzer가 부분 판정만 확보했습니다. 신뢰도 {confidence}%."

    # partial이 아닌 새 근거에서 기종 불일치가 남아 있으면 보수적으로 재분류 대상으로 둔다.
    if db_core or db_platform:
        if not _health_core_equivalent(db_core, db_platform, core, platform):
            return "reclassify_required", f"현재 등록: {db_platform or db_core} / 최신 판정: {platform or core} ({core})"

    required_bios = _normalize_required_archive(rom_info.get("needed_bios") or "")
    bios_mandatory = bool(rom_info.get("bios_mandatory") or rom_info.get("bios_needed"))
    if required_bios and bios_mandatory and not _is_optional_runtime_bios(required_bios):
        bios_names = available_bios_names or set()
        if not _is_required_bios_available(required_bios, bios_names, bios_dir):
            return "bios_required", required_bios

    required_chd = str(rom_info.get("required_chd") or "").strip()
    if required_chd and not _is_required_chd_available(file_path, required_chd):
        return "chd_required", required_chd

    unsupported_reason = _emulatorjs_unsupported_reason(rom_info)
    if unsupported_reason:
        return "unsupported", unsupported_reason

    if rom_info.get("is_playable") is False:
        return "unsupported", "rom-analyzer가 이 파일을 직접 실행 가능한 게임 ROM으로 판정하지 않았습니다."

    return "pass", ""


def _build_analysis_snapshot(analysis, health_status="", health_reason="", file_state="ok", cache_key="", analyzed_at=""):
    """ROM 분석의 단일 저장 원본을 만든다.

    rom-analyzer 상세 근거와 그 결과에서 파생한 health 요약을 같은 JSON에 보관한다.
    목록용 health_status/missing_roms 컬럼은 이 스냅샷의 DB 인덱스 역할만 한다.
    """
    analysis = analysis if isinstance(analysis, dict) else {}

    def _text(value):
        return str(value or "").strip()

    def _text_list(value, basename=False):
        items = value if isinstance(value, (list, tuple, set)) else ([] if value in (None, "") else [value])
        result = []
        for item in items:
            text = _text(item)
            if not text:
                continue
            if basename:
                text = os.path.basename(text)
            if text not in result:
                result.append(text)
        return result

    snapshot = {
        "core": _text(analysis.get("core")),
        "platform": _text(analysis.get("platform")),
        "title": _text(analysis.get("title")),
        "game_code": _text(analysis.get("game_code")),
        "needed_bios": _text(analysis.get("needed_bios")),
        "bios_mandatory": bool(analysis.get("bios_mandatory")),
        "bios_needed": bool(analysis.get("bios_needed")),
        "parent_hint": _text(analysis.get("parent_hint")),
        "required_chd": _text(analysis.get("required_chd")),
        "matched_count": int(analysis.get("matched_count") or 0),
        "total_roms": int(analysis.get("total_roms") or 0),
        "match_rate": float(analysis.get("match_rate") or 0.0),
        "serial_code": _text(analysis.get("serial_code")),
        "source_system": _text(analysis.get("source_system")),
        "metadata_source": _text(analysis.get("metadata_source")),
        "metadata_confidence": int(analysis.get("metadata_confidence") or 0),
        "disk_missing_files": _text_list(analysis.get("disk_missing_files"), basename=True),
        "resolved_disk_files": _text_list(analysis.get("resolved_disk_files"), basename=True),
        "disc_count": int(analysis.get("disc_count") or 0),
        "identity_status": _text(analysis.get("identity_status")),
        "analysis_methods": _text_list(analysis.get("analysis_methods")),
        "analysis_warnings": _text_list(analysis.get("analysis_warnings")),
        "analysis_conflicts": _text_list(analysis.get("analysis_conflicts")),
        "is_playable": bool(analysis.get("is_playable")),
        "emulatorjs_supported": bool(analysis.get("emulatorjs_supported")),
        "emulatorjs_core": _text(analysis.get("emulatorjs_core")),
        "emulatorjs_system": _text(analysis.get("emulatorjs_system")),
        "emulatorjs_reason": _text(analysis.get("emulatorjs_reason")),
    }
    snapshot.update({
        "health_status": _text(health_status or analysis.get("health_status")),
        "health_reason": _text(health_reason or analysis.get("health_reason")),
        "file_state": _text(file_state or analysis.get("file_state") or "ok"),
        "analysis_cache_key": _text(cache_key or analysis.get("analysis_cache_key")),
        "analysis_updated_at": _text(analyzed_at or analysis.get("analysis_updated_at")),
    })
    return snapshot


class BookoasisGamebooksMetadataProvider(BaseMetadataProvider):
    id = "bookoasis_gamebooks"
    name = "Game Books"
    is_searchable = False
    DB_SCHEMA_VERSION = 3

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
        # Game Books는 중첩 libs/와 SQLite 바이너리를 함께 배포하므로
        # BookOasis 코어의 텍스트 기반 샘플 업데이트 버튼은 사용하지 않는다.
        # 온라인/ZIP 업데이트는 Plugin Manager의 저장소 ZIP 경로를 사용한다.
        "show_sample_update_button": False,
        "files": [
            "bookoasis_gamebooks.py",
            "__init__.py",
            "VERSION",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
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
            "libs/rom_analyzer/arcade/compatibility.py",
            "libs/rom_analyzer/arcade/dat_matcher.py",
            "libs/rom_analyzer/arcade/database.py",
            "libs/rom_analyzer/arcade/detector.py",
            "libs/rom_analyzer/core_info.py",
            "libs/rom_analyzer/database_context.py",
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
            "libs/library_structures/__init__.py",
            "libs/library_structures/base.py",
            "libs/library_structures/manager.py",
            "libs/library_structures/models.py",
            "libs/library_structures/romm.py",
            "libs/rom_database/VENDORED_FROM.json",
            "libs/rom_database/__init__.py",
            "libs/rom_database/__main__.py",
            "libs/rom_database/builders/__init__.py",
            "libs/rom_database/builders/compatibility.py",
            "libs/rom_database/builders/metadata.py",
            "libs/rom_database/catalogs/__init__.py",
            "libs/rom_database/catalogs/arcade.py",
            "libs/rom_database/catalogs/bios.py",
            "libs/rom_database/connection.py",
            "libs/rom_database/data/arcade_dat.db",
            "libs/rom_database/data/mame_compatibility.db",
            "libs/rom_database/data/rom_metadata.db",
            "libs/rom_database/manager.py",
            "libs/rom_database/models.py",
            "libs/rom_database/paths.py",
            "libs/rom_database/repositories/__init__.py",
            "libs/rom_database/repositories/compatibility.py",
            "libs/rom_database/repositories/dat.py",
            "libs/rom_database/repositories/metadata.py",
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

    def _get_library_manager(self):
        """Game Books 표준 물리 배치를 담당하는 library_structures 관리자."""
        from library_structures import LibraryManager

        return LibraryManager(self._get_emulatorjs_root())

    def _place_new_ingest_content(self, analysis_result, future_id, file_path):
        """신규 ingest ROM을 재분석 없이 최종 game-id 기반 구조로 배치한다.

        기존 정책 단계에서 7z 변환/디스크 번들 정리가 끝난 뒤 호출되므로
        원본 RomAnalysisResult의 파일 위치와 sidecar 참조만 현재 상태로 맞춘다.
        """
        if analysis_result is None:
            return None

        source_path = os.path.abspath(str(file_path or ""))
        if not source_path or not os.path.isfile(source_path):
            return None

        analysis_result.file_path = source_path
        analysis_result.file_name = os.path.basename(source_path)
        analysis_result.file_size = os.path.getsize(source_path)
        analysis_result.file_ext = os.path.splitext(source_path)[1].lower()

        disc_info = getattr(analysis_result, "disc_info", None)
        if disc_info is not None and getattr(analysis_result, "is_disc", False):
            # 기존 Game Books 번들 이동은 모든 sidecar를 같은 폴더로 모으고
            # CUE/GDI/M3U 참조를 basename으로 재작성한다. 재분석 없이 raw 결과도
            # 같은 표현으로 맞춰 library_structures가 현재 파일들을 찾게 한다.
            refs = list(getattr(disc_info, "referenced_files", None) or [])
            if refs:
                disc_info.referenced_files = [os.path.basename(str(ref)) for ref in refs]
            playlist = list(getattr(disc_info, "playlist_entries", None) or [])
            if playlist:
                disc_info.playlist_entries = [os.path.basename(str(ref)) for ref in playlist]

        return self._get_library_manager().place_content(
            analysis_result,
            future_id,
            move_files=True,
            conflict_strategy="replace",
        )

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

    def _build_cover_file_index(self):
        """커버 디렉터리를 한 번만 읽어 요청 단위 재사용 인덱스를 만든다."""
        covers_dir = self._get_covers_dir()
        entries = []
        by_name = {}
        try:
            for entry in os.scandir(covers_dir):
                if not entry.is_file():
                    continue
                name = entry.name.lower()
                if os.path.splitext(name)[1] not in (".png", ".jpg", ".jpeg", ".webp"):
                    continue
                path = entry.path
                entries.append((name, path))
                by_name.setdefault(name, path)
        except OSError as e:
            logger.debug(f"[{SELF_ID}] Cover index scan error ({covers_dir}): {e}")
        return {"covers_dir": covers_dir, "entries": entries, "by_name": by_name}

    def _resolve_existing_cover(self, game_id, filename="", platform_or_core="", current_cover_path="", update_db=False, cover_index=None):
        """경로 기반 game_id가 바뀐 경우에도 기존 커버 파일을 안전하게 재연결한다."""
        if current_cover_path and os.path.isfile(current_cover_path):
            return current_cover_path
        covers_dir = (cover_index or {}).get("covers_dir") or self._get_covers_dir()
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            indexed_exact = None
            if cover_index is not None:
                indexed_exact = (cover_index.get("by_name") or {}).get(f"{game_id}{ext}".lower())
            exact = indexed_exact or os.path.join(covers_dir, f"{game_id}{ext}")
            if indexed_exact or (cover_index is None and os.path.isfile(exact)):
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
            if cover_index is not None:
                cover_entries = cover_index.get("entries") or []
            else:
                cover_entries = []
                for entry in os.scandir(covers_dir):
                    if not entry.is_file():
                        continue
                    name = entry.name.lower()
                    if os.path.splitext(name)[1] not in (".png", ".jpg", ".jpeg", ".webp"):
                        continue
                    cover_entries.append((name, entry.path))

            for name, entry_path in cover_entries:
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
                candidates.append((score, entry_path))
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

    def _rom_path_folder_aliases(self, platform_or_core):
        key = str(platform_or_core or "").lower().replace("-", "").replace("_", "")
        groups = {
            "arcade": ["arcade", "mame2003", "neogeo"],
            "mame2003": ["mame2003", "arcade", "neogeo"],
            "neogeo": ["neogeo", "arcade", "mame2003"],
            "genesis": ["megadriv", "segamd", "genesis"],
            "segamd": ["megadriv", "segamd", "genesis"],
            "megadrive": ["megadriv", "segamd", "genesis"],
            "ps1": ["psx", "ps1"], "psx": ["psx", "ps1"],
            "saturn": ["saturn", "segasaturn"],
            "gb": ["gb"], "gbc": ["gbc"], "gba": ["gba"],
            "nes": ["nes"], "snes": ["snes"], "n64": ["n64"],
            "pce": ["pce"], "dreamcast": ["dreamcast"],
        }
        return groups.get(key, [key] if key else [])

    def _resolve_existing_rom_path(self, game_id, filename="", current_path="", core="", platform="", update_db=False):
        """DB의 ROM 경로가 끊긴 경우 동일 파일명을 라이브러리 루트에서 안전하게 재탐색한다."""
        if current_path and os.path.isfile(current_path):
            return os.path.abspath(current_path)
        raw_filename = os.path.basename(str(filename or current_path or "")).strip()
        if not raw_filename:
            return None

        roots = []
        for candidate in (self._get_roms_dir(), self._get_setting("EXTRA_ROMS_PATH", "").strip()):
            if candidate and os.path.isdir(candidate):
                real = os.path.realpath(os.path.abspath(candidate))
                if real not in roots:
                    roots.append(real)

        aliases = []
        for value in (core, platform):
            for alias in self._rom_path_folder_aliases(value):
                if alias and alias not in aliases:
                    aliases.append(alias)

        candidates = []
        wanted = raw_filename.casefold()
        for root_dir in roots:
            try:
                for walk_root, dirs, files in os.walk(root_dir):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    matches = [f for f in files if f.casefold() == wanted]
                    for match in matches:
                        path = os.path.abspath(os.path.join(walk_root, match))
                        rel_parts = [part.lower().replace("-", "").replace("_", "") for part in os.path.relpath(path, root_dir).split(os.sep)[:-1]]
                        score = 100
                        for idx, alias in enumerate(aliases):
                            norm_alias = alias.lower().replace("-", "").replace("_", "")
                            if norm_alias in rel_parts:
                                score += max(1, 30 - idx)
                                break
                        candidates.append((score, path))
            except Exception as exc:
                logger.debug(f"[{SELF_ID}] ROM path fallback scan error ({raw_filename}): {exc}")

        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1].lower()))
        best_score = candidates[0][0]
        best = [path for score, path in candidates if score == best_score]
        if len(best) != 1:
            logger.warning(f"[{SELF_ID}] ROM path fallback ambiguous ({raw_filename}): {best}")
            return None
        best_path = best[0]
        if update_db:
            self._db_execute("UPDATE games SET file_path = ? WHERE id = ?", (best_path, game_id))
            logger.info(f"[{SELF_ID}] ROM path recovered: {game_id} -> {best_path}")
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

    def _list_available_bios_names(self):
        """BIOS 관리/클라이언트 판정용 파일명 목록을 경로 노출 없이 반환한다."""
        bios_dir = self._get_bios_dir()
        if not bios_dir or not os.path.isdir(bios_dir):
            return []
        try:
            return sorted({
                entry.name.lower()
                for entry in os.scandir(bios_dir)
                if entry.is_file() and not entry.name.startswith(".")
            })
        except OSError as e:
            logger.warning(f"[{SELF_ID}] BIOS file list error ({bios_dir}): {e}")
            return []

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

    @staticmethod
    def _ensure_db_column(conn, table, column, definition):
        """누락 컬럼만 추가하며 예상하지 못한 SQLite 오류를 숨기지 않는다."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(table or "")):
            raise ValueError(f"잘못된 테이블 이름: {table}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(column or "")):
            raise ValueError(f"잘못된 컬럼 이름: {column}")
        existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @classmethod
    def _schema_migration_columns(cls, version):
        if version == 1:
            return (
                ("core", "TEXT DEFAULT 'gba'"), ("platform", "TEXT DEFAULT 'GBA'"),
                ("mtime", "REAL DEFAULT 0"), ("needed_bios", "TEXT"),
                ("health_status", "TEXT DEFAULT 'pass'"), ("missing_roms", "TEXT DEFAULT ''"),
                ("rom_crc32", "TEXT DEFAULT ''"), ("rom_md5", "TEXT DEFAULT ''"),
                ("rom_sha1", "TEXT DEFAULT ''"), ("serial_code", "TEXT DEFAULT ''"),
                ("normalized_title", "TEXT DEFAULT ''"), ("source_system", "TEXT DEFAULT ''"),
                ("metadata_source", "TEXT DEFAULT ''"), ("metadata_confidence", "INTEGER DEFAULT 0"),
                ("canonical_title", "TEXT DEFAULT ''"), ("alt_titles", "TEXT DEFAULT ''"),
                ("region", "TEXT DEFAULT ''"), ("genre", "TEXT DEFAULT ''"),
                ("developer", "TEXT DEFAULT ''"), ("publisher", "TEXT DEFAULT ''"),
                ("release_year", "TEXT DEFAULT ''"), ("players", "INTEGER DEFAULT 0"),
                ("description", "TEXT DEFAULT ''"), ("region_tag", "TEXT DEFAULT ''"),
                ("revision_tag", "TEXT DEFAULT ''"), ("disc_number", "INTEGER DEFAULT 0"),
                ("content_flags", "TEXT DEFAULT ''"), ("future_id", "INTEGER"),
                ("layout_version", "INTEGER DEFAULT 1"), ("cover_thumbnail_path", "TEXT DEFAULT ''"),
                ("cover_revision", "INTEGER DEFAULT 0"), ("health_cache_key", "TEXT DEFAULT ''"),
            )
        if version == 2:
            return (
                ("deletion_status", "TEXT DEFAULT 'active'"), ("deletion_requested_at", "TEXT DEFAULT ''"),
                ("deletion_requested_by", "INTEGER DEFAULT 0"), ("deletion_error", "TEXT DEFAULT ''"),
                ("analysis_json", "TEXT DEFAULT ''"), ("analysis_cache_key", "TEXT DEFAULT ''"),
                ("play_status", "TEXT DEFAULT 'untested'"), ("play_status_updated_at", "TEXT DEFAULT ''"),
                ("play_status_user_id", "INTEGER DEFAULT 0"), ("play_status_health_key", "TEXT DEFAULT ''"),
                ("last_booted_at", "TEXT DEFAULT ''"),
            )
        if version == 3:
            return (
                ("content_identity_key", "TEXT DEFAULT ''"),
                ("play_status_content_key", "TEXT DEFAULT ''"),
                ("cover_large_path", "TEXT DEFAULT ''"),
            )
        raise ValueError(f"지원하지 않는 DB 스키마 버전: {version}")

    def _run_schema_migrations(self, conn):
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute("INSERT OR IGNORE INTO schema_meta(singleton, version) VALUES (1, 0)")
        row = conn.execute("SELECT version FROM schema_meta WHERE singleton = 1").fetchone()
        current = int(row[0] if row else 0)
        if current > self.DB_SCHEMA_VERSION:
            raise RuntimeError(f"DB 스키마가 플러그인보다 최신입니다: DB={current}, plugin={self.DB_SCHEMA_VERSION}")

        for version in range(current + 1, self.DB_SCHEMA_VERSION + 1):
            for column, definition in self._schema_migration_columns(version):
                self._ensure_db_column(conn, "games", column, definition)
            if version == 3:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS library_migrations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        migration_name TEXT NOT NULL DEFAULT 'phase6',
                        future_id INTEGER NOT NULL,
                        legacy_id TEXT NOT NULL,
                        source_path TEXT DEFAULT '',
                        target_path TEXT DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        stage TEXT NOT NULL DEFAULT 'planned',
                        transport TEXT DEFAULT '',
                        source_size INTEGER DEFAULT 0,
                        attempts INTEGER DEFAULT 0,
                        last_error TEXT DEFAULT '',
                        started_at TEXT DEFAULT '',
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT DEFAULT '',
                        UNIQUE(migration_name, future_id)
                    )"""
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_library_migrations_status ON library_migrations(migration_name, status)")
            conn.execute(
                "UPDATE schema_meta SET version = ?, updated_at = CURRENT_TIMESTAMP WHERE singleton = 1",
                (version,),
            )

    @staticmethod
    def _content_identity_payload(game, analysis_snapshot=None):
        game = game or {}
        snapshot = analysis_snapshot if isinstance(analysis_snapshot, dict) else {}
        sha1 = str(game.get("rom_sha1") or "").strip().lower()
        md5 = str(game.get("rom_md5") or "").strip().lower()
        crc32 = str(game.get("rom_crc32") or "").strip().lower()
        strong = sha1 or md5 or crc32
        resolved = sorted({os.path.basename(str(x)) for x in (snapshot.get("resolved_disk_files") or []) if str(x or "").strip()})
        return {
            "v": 1,
            "core": str(game.get("core") or snapshot.get("core") or "").strip().lower(),
            "platform": str(game.get("platform") or snapshot.get("platform") or "").strip().lower(),
            "size": int(game.get("size_bytes") or 0),
            "sha1": sha1,
            "md5": md5,
            "crc32": crc32,
            "game_code": str(game.get("game_code") or snapshot.get("game_code") or "").strip(),
            "serial": str(game.get("serial_code") or snapshot.get("serial_code") or "").strip(),
            "bundle": resolved,
            "required_chd": str(snapshot.get("required_chd") or "").strip().lower(),
            "fallback": "" if strong else "|".join((
                str(game.get("normalized_title") or "").strip().casefold(),
                os.path.basename(str(game.get("filename") or "")).casefold(),
            )),
        }

    @classmethod
    def _content_identity_key(cls, game, analysis_snapshot=None):
        payload = cls._content_identity_payload(game, analysis_snapshot=analysis_snapshot)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _backfill_content_identity(self, conn):
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(games)").fetchall()}
        if "content_identity_key" not in columns:
            return 0
        rows = conn.execute(
            """SELECT id, filename, core, platform, size_bytes, game_code, rom_crc32, rom_md5, rom_sha1,
                      serial_code, normalized_title, analysis_json, content_identity_key,
                      play_status, play_status_content_key
               FROM games"""
        ).fetchall()
        updated = 0
        for row in rows:
            keys = ["id", "filename", "core", "platform", "size_bytes", "game_code", "rom_crc32", "rom_md5", "rom_sha1",
                    "serial_code", "normalized_title", "analysis_json", "content_identity_key", "play_status", "play_status_content_key"]
            game = dict(zip(keys, row))
            snapshot = {}
            try:
                parsed = json.loads(game.get("analysis_json") or "{}")
                snapshot = parsed if isinstance(parsed, dict) else {}
            except Exception:
                snapshot = {}
            identity_key = self._content_identity_key(game, snapshot)
            play_key = str(game.get("play_status_content_key") or "")
            raw_status = str(game.get("play_status") or "untested")
            desired_play_key = play_key or (identity_key if raw_status != "untested" else "")
            if str(game.get("content_identity_key") or "") != identity_key or play_key != desired_play_key:
                conn.execute(
                    "UPDATE games SET content_identity_key = ?, play_status_content_key = ? WHERE id = ?",
                    (identity_key, desired_play_key, game.get("id")),
                )
                updated += 1
        return updated

    def _init_db(self):
        """SQLite 스키마를 명시적 버전 마이그레이션으로 초기화하고 Phase 6 불변식을 검증한다."""
        db_path = self._get_db_path()
        with _DB_LOCK:
            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("BEGIN")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS games (
                        id TEXT PRIMARY KEY, filename TEXT, file_path TEXT, title TEXT, game_code TEXT,
                        maker_code TEXT, core TEXT DEFAULT 'gba', platform TEXT DEFAULT 'GBA',
                        size_bytes INTEGER DEFAULT 0, mtime REAL DEFAULT 0, added_at TEXT,
                        cover_path TEXT, needed_bios TEXT
                    )"""
                )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS user_game_data (
                        user_id INTEGER, game_id TEXT, is_favorite INTEGER DEFAULT 0,
                        last_played_at TEXT, play_count INTEGER DEFAULT 0,
                        PRIMARY KEY (user_id, game_id)
                    )"""
                )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS game_id_map (
                        future_id INTEGER PRIMARY KEY AUTOINCREMENT, legacy_id TEXT NOT NULL UNIQUE,
                        migration_status TEXT NOT NULL DEFAULT 'pending', last_error TEXT DEFAULT '',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
                self._run_schema_migrations(conn)
                self._backfill_future_game_ids(conn)
                self._backfill_content_identity(conn)
                # game_id_map.future_id가 영구 ID의 UNIQUE 소유권을 보장한다.
                # games는 relocation/rebind 중 짧은 전환 구간에 동일 future_id가 두 행에
                # 존재할 수 있으므로 UNIQUE 인덱스를 걸지 않고 Phase 6 preflight에서
                # 최종 불변식(중복 0)을 강제한다.
                conn.execute("CREATE INDEX IF NOT EXISTS idx_games_future_id ON games(future_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_games_deletion_status ON games(deletion_status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_games_content_identity ON games(content_identity_key)")
                conn.commit()
            except Exception as e:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                logger.error(f"[{SELF_ID}] DB Init error: {e}")
                raise
            finally:
                if conn is not None:
                    conn.close()

    # ------------------------------------------------------------------
    # DB 헬퍼 함수
    # ------------------------------------------------------------------
    def _backfill_future_game_ids(self, conn):
        """기존 문자열 game id에 최종 INTEGER ID를 한 번만 영구 예약한다.

        기존 설치는 현재 SQLite rowid를 우선 사용해 번호를 안정적으로 보존하고,
        이미 game_id_map에 고정된 매핑이 있으면 그 값을 최우선으로 사용한다.
        """
        rows = conn.execute(
            "SELECT rowid, id, future_id FROM games ORDER BY rowid"
        ).fetchall()
        for source_rowid, raw_legacy_id, stored_future_id in rows:
            legacy_id = str(raw_legacy_id or "").strip()
            if not legacy_id:
                continue

            mapped = conn.execute(
                "SELECT future_id FROM game_id_map WHERE legacy_id = ?",
                (legacy_id,),
            ).fetchone()
            if mapped:
                future_id = int(mapped[0])
                if int(stored_future_id or 0) != future_id:
                    conn.execute(
                        "UPDATE games SET future_id = ? WHERE id = ?",
                        (future_id, legacy_id),
                    )
                continue

            preferred_id = int(stored_future_id or source_rowid or 0)
            future_id = 0
            if preferred_id > 0:
                owner = conn.execute(
                    "SELECT legacy_id FROM game_id_map WHERE future_id = ?",
                    (preferred_id,),
                ).fetchone()
                if not owner:
                    try:
                        conn.execute(
                            """INSERT INTO game_id_map
                               (future_id, legacy_id, migration_status, updated_at)
                               VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)""",
                            (preferred_id, legacy_id),
                        )
                        future_id = preferred_id
                    except sqlite3.IntegrityError:
                        future_id = 0
                elif str(owner[0] or "") == legacy_id:
                    future_id = preferred_id

            if future_id <= 0:
                cur = conn.execute(
                    """INSERT INTO game_id_map
                       (legacy_id, migration_status, updated_at)
                       VALUES (?, 'pending', CURRENT_TIMESTAMP)""",
                    (legacy_id,),
                )
                future_id = int(cur.lastrowid)

            if int(stored_future_id or 0) != future_id:
                conn.execute(
                    "UPDATE games SET future_id = ? WHERE id = ?",
                    (future_id, legacy_id),
                )

    def _get_or_create_future_game_id(self, legacy_id):
        """현재 문자열 ID에 대응하는 영구 INTEGER game id를 반환한다."""
        legacy_id = str(legacy_id or "").strip()
        if not legacy_id:
            raise ValueError("legacy game id가 필요합니다.")

        with _DB_LOCK:
            conn = self._get_db_conn(timeout=60)
            try:
                mapped = conn.execute(
                    "SELECT future_id FROM game_id_map WHERE legacy_id = ?",
                    (legacy_id,),
                ).fetchone()
                if mapped:
                    return int(mapped[0])

                existing = conn.execute(
                    "SELECT future_id FROM games WHERE id = ?",
                    (legacy_id,),
                ).fetchone()
                preferred_id = int((existing[0] if existing else 0) or 0)
                future_id = 0
                if preferred_id > 0:
                    owner = conn.execute(
                        "SELECT legacy_id FROM game_id_map WHERE future_id = ?",
                        (preferred_id,),
                    ).fetchone()
                    if not owner:
                        try:
                            conn.execute(
                                """INSERT INTO game_id_map
                                   (future_id, legacy_id, migration_status, updated_at)
                                   VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)""",
                                (preferred_id, legacy_id),
                            )
                            future_id = preferred_id
                        except sqlite3.IntegrityError:
                            future_id = 0
                    elif str(owner[0] or "") == legacy_id:
                        future_id = preferred_id

                if future_id <= 0:
                    cur = conn.execute(
                        """INSERT INTO game_id_map
                           (legacy_id, migration_status, updated_at)
                           VALUES (?, 'pending', CURRENT_TIMESTAMP)""",
                        (legacy_id,),
                    )
                    future_id = int(cur.lastrowid)

                conn.execute(
                    "UPDATE games SET future_id = ? WHERE id = ?",
                    (future_id, legacy_id),
                )
                conn.commit()
                return future_id
            finally:
                conn.close()

    def _rebind_future_game_id(self, old_legacy_id, new_legacy_id, future_id):
        """경로 기반 legacy ID가 바뀌어도 같은 영구 INTEGER ID를 유지한다."""
        old_legacy_id = str(old_legacy_id or "").strip()
        new_legacy_id = str(new_legacy_id or "").strip()
        future_id = int(future_id or 0)
        if not old_legacy_id or not new_legacy_id or future_id <= 0:
            raise ValueError("future game id 재바인딩 인자가 올바르지 않습니다.")
        if old_legacy_id == new_legacy_id:
            actual_id = self._get_or_create_future_game_id(new_legacy_id)
            if actual_id != future_id:
                raise ValueError(
                    f"future game id 충돌: {new_legacy_id}={actual_id}, expected={future_id}"
                )
            return actual_id

        with _DB_LOCK:
            conn = self._get_db_conn(timeout=60)
            try:
                mapped_new = conn.execute(
                    "SELECT future_id FROM game_id_map WHERE legacy_id = ?",
                    (new_legacy_id,),
                ).fetchone()
                if mapped_new and int(mapped_new[0]) != future_id:
                    raise ValueError(
                        f"legacy id가 다른 future id에 이미 연결되어 있습니다: "
                        f"{new_legacy_id} -> {mapped_new[0]}"
                    )

                mapped_future = conn.execute(
                    "SELECT legacy_id FROM game_id_map WHERE future_id = ?",
                    (future_id,),
                ).fetchone()
                if mapped_future:
                    owner = str(mapped_future[0] or "")
                    if owner not in {old_legacy_id, new_legacy_id}:
                        raise ValueError(
                            f"future id가 다른 legacy id에 이미 연결되어 있습니다: "
                            f"{future_id} -> {owner}"
                        )
                    conn.execute(
                        """UPDATE game_id_map
                           SET legacy_id = ?, migration_status = 'pending', last_error = '',
                               updated_at = CURRENT_TIMESTAMP
                           WHERE future_id = ?""",
                        (new_legacy_id, future_id),
                    )
                else:
                    mapped_old = conn.execute(
                        "SELECT future_id FROM game_id_map WHERE legacy_id = ?",
                        (old_legacy_id,),
                    ).fetchone()
                    if mapped_old and int(mapped_old[0]) != future_id:
                        raise ValueError(
                            f"기존 legacy id의 future id가 일치하지 않습니다: "
                            f"{old_legacy_id} -> {mapped_old[0]}"
                        )
                    conn.execute(
                        """INSERT INTO game_id_map
                           (future_id, legacy_id, migration_status, updated_at)
                           VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)""",
                        (future_id, new_legacy_id),
                    )

                conn.execute(
                    "UPDATE games SET future_id = ? WHERE id = ?",
                    (future_id, new_legacy_id),
                )
                conn.commit()
                return future_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _delete_future_game_id(self, legacy_id):
        """실제 게임 삭제 후 legacy 매핑만 제거한다. AUTOINCREMENT ID는 재사용하지 않는다."""
        legacy_id = str(legacy_id or "").strip()
        if not legacy_id:
            return False
        with _DB_LOCK:
            conn = self._get_db_conn(timeout=60)
            try:
                still_exists = conn.execute(
                    "SELECT 1 FROM games WHERE id = ? LIMIT 1",
                    (legacy_id,),
                ).fetchone()
                if still_exists:
                    return False
                cur = conn.execute(
                    "DELETE FROM game_id_map WHERE legacy_id = ?",
                    (legacy_id,),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

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

    def _db_execute_batches(self, batches):
        """동일 SQL의 여러 작업을 단일 SQLite 연결/트랜잭션으로 일괄 반영한다."""
        normalized = []
        for query, args_rows in (batches or []):
            rows = list(args_rows or [])
            if query and rows:
                normalized.append((query, rows))
        if not normalized:
            return 0

        with _DB_LOCK:
            conn = self._get_db_conn(timeout=60)
            try:
                affected = 0
                for query, args_rows in normalized:
                    cur = conn.executemany(query, args_rows)
                    if cur.rowcount and cur.rowcount > 0:
                        affected += cur.rowcount
                conn.commit()
                return affected
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _ensure_cover_variants(self, game_id, force=False):
        """원본 커버를 보존하면서 future_id 기반 small/large WebP를 생성한다."""
        rows = self._db_query(
            """SELECT id, future_id, cover_path, COALESCE(cover_large_path,'') AS cover_large_path,
                      COALESCE(cover_thumbnail_path,'') AS cover_thumbnail_path,
                      COALESCE(cover_revision,0) AS cover_revision
               FROM games WHERE id = ?""",
            (game_id,),
        )
        if not rows:
            return {"success": False, "error": "게임을 찾을 수 없습니다."}
        game = rows[0]
        future_id = int(game.get("future_id") or 0)
        if future_id <= 0:
            return {"success": False, "error": "future_id가 준비되지 않았습니다."}
        source = str(game.get("cover_path") or "").strip()
        large = str(game.get("cover_large_path") or "").strip()
        small = str(game.get("cover_thumbnail_path") or "").strip()
        if not force and large and small and os.path.isfile(large) and os.path.isfile(small):
            return {"success": True, "cached": True, "large": large, "small": small, "revision": int(game.get("cover_revision") or 0)}
        if not source or not os.path.isfile(source):
            return {"success": False, "error": "원본 커버 파일을 찾을 수 없습니다."}

        result = self._get_library_manager().save_cover(future_id, source)
        if not result or not result.success or not result.cover_l_dest_path or not result.cover_s_dest_path:
            errors = list(getattr(result, "errors", None) or []) if result is not None else []
            return {"success": False, "error": "; ".join(errors) or "WebP 커버 생성에 실패했습니다."}
        self._db_execute(
            """UPDATE games SET cover_large_path = ?, cover_thumbnail_path = ?,
                      cover_revision = COALESCE(cover_revision, 0) + 1
               WHERE id = ?""",
            (os.path.abspath(result.cover_l_dest_path), os.path.abspath(result.cover_s_dest_path), game_id),
        )
        updated = self._db_query("SELECT cover_revision FROM games WHERE id = ?", (game_id,))
        revision = int(updated[0].get("cover_revision") or 0) if updated else int(game.get("cover_revision") or 0) + 1
        return {
            "success": True, "cached": False,
            "large": os.path.abspath(result.cover_l_dest_path),
            "small": os.path.abspath(result.cover_s_dest_path),
            "revision": revision,
        }

    def _finalize_cover_source(self, game_id, cover_path, force=False):
        if cover_path:
            try:
                self._ensure_cover_variants(game_id, force=force)
            except Exception as exc:
                logger.debug(f"[{SELF_ID}] WebP cover variant skip ({game_id}): {exc}")
        return cover_path

    def _rebuild_cover_variants_worker(self, force=False):
        rows = self._db_query(
            """SELECT id, title, filename, cover_path, cover_large_path, cover_thumbnail_path
               FROM games WHERE COALESCE(deletion_status,'active')='active' AND COALESCE(cover_path,'')<>'' ORDER BY id"""
        )
        total = len(rows)
        with _COVER_VARIANT_PROGRESS_LOCK:
            _COVER_VARIANT_PROGRESS.update({
                "is_running": True, "current": 0, "total": total, "completed": 0, "failed": 0,
                "current_title": "", "status": "running", "updated_at": time.time(),
            })
        try:
            for index, game in enumerate(rows, start=1):
                title = str(game.get("title") or game.get("filename") or game.get("id") or "")
                with _COVER_VARIANT_PROGRESS_LOCK:
                    _COVER_VARIANT_PROGRESS.update({"current": index - 1, "current_title": title, "updated_at": time.time()})
                try:
                    result = self._ensure_cover_variants(game.get("id"), force=force)
                    with _COVER_VARIANT_PROGRESS_LOCK:
                        if result.get("success"):
                            _COVER_VARIANT_PROGRESS["completed"] += 1
                        else:
                            _COVER_VARIANT_PROGRESS["failed"] += 1
                except Exception as exc:
                    logger.debug(f"[{SELF_ID}] WebP cover rebuild failed ({game.get('id')}): {exc}")
                    with _COVER_VARIANT_PROGRESS_LOCK:
                        _COVER_VARIANT_PROGRESS["failed"] += 1
                with _COVER_VARIANT_PROGRESS_LOCK:
                    _COVER_VARIANT_PROGRESS["current"] = index
                    _COVER_VARIANT_PROGRESS["updated_at"] = time.time()
        finally:
            with _COVER_VARIANT_PROGRESS_LOCK:
                _COVER_VARIANT_PROGRESS.update({
                    "is_running": False, "current_title": "", "status": "completed", "updated_at": time.time(),
                })

    def _start_cover_variant_rebuild(self, force=False):
        with _COVER_VARIANT_PROGRESS_LOCK:
            if _COVER_VARIANT_PROGRESS.get("is_running"):
                return {"success": True, "started": False, "progress": dict(_COVER_VARIANT_PROGRESS)}
            _COVER_VARIANT_PROGRESS.update({
                "is_running": True, "current": 0, "total": 0, "completed": 0, "failed": 0,
                "current_title": "준비 중...", "status": "queued", "updated_at": time.time(),
            })
        threading.Thread(target=self._rebuild_cover_variants_worker, args=(bool(force),), daemon=True).start()
        return {"success": True, "started": True, "progress": dict(_COVER_VARIANT_PROGRESS)}

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
                return self._finalize_cover_source(game_id, existing_cover)

            covers_dir = self._get_covers_dir()
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                existing_cover = os.path.join(covers_dir, f"{game_id}{ext}")
                if os.path.exists(existing_cover):
                    self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (existing_cover, game_id))
                    return self._finalize_cover_source(game_id, existing_cover)

            # 1차: Libretro Thumbnails CDN (무료/API Key 불필요)
            art_bytes = _fetch_libretro_artwork(platform_or_core, filename, raw_title=raw_title)
            if art_bytes:
                save_cover_path = os.path.join(covers_dir, f"{game_id}.png")
                with open(save_cover_path, "wb") as f:
                    f.write(art_bytes)
                self._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (save_cover_path, game_id))
                logger.info(f"[{SELF_ID}] Auto-fetched Libretro cover for {filename} -> {save_cover_path}")
                return self._finalize_cover_source(game_id, save_cover_path, force=True)

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
                    return self._finalize_cover_source(game_id, save_cover_path, force=True)

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
                    return self._finalize_cover_source(game_id, save_cover_path, force=True)

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
            self._delete_future_game_id(game_id)
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

    def _health_engine_signature(self, available_bios_names):
        """진단 결과 캐시를 무효화할 analyzer/DB 전역 서명을 만든다."""
        revision_parts = []
        for rel in (
            "libs/rom_analyzer/VENDORED_FROM.json",
            "libs/rom_database/VENDORED_FROM.json",
        ):
            path = Path(_PLUGIN_DIR) / rel
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                revision_parts.append(
                    f"{rel}:{data.get('version', '')}:{data.get('git_commit', '')}"
                )
            except Exception:
                try:
                    st = path.stat()
                    revision_parts.append(f"{rel}:{st.st_size}:{st.st_mtime_ns}")
                except Exception:
                    revision_parts.append(f"{rel}:missing")

        db_dir = Path(_PLUGIN_DIR) / "libs" / "rom_database" / "data"
        if db_dir.is_dir():
            for db_file in sorted(db_dir.glob("*.db")):
                try:
                    st = db_file.stat()
                    revision_parts.append(f"{db_file.name}:{st.st_size}:{st.st_mtime_ns}")
                except Exception:
                    revision_parts.append(f"{db_file.name}:unreadable")

        payload = json.dumps(
            {"engine": revision_parts},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _health_cache_key(game, engine_signature, state, resolved_path="", stat_result=None, bios_state="", bundle_state=""):
        """파일/등록 상태와 분석 엔진 상태가 같을 때 재분석을 건너뛸 키를 만든다."""
        stat_size = int(getattr(stat_result, "st_size", 0) or 0)
        stat_mtime_ns = int(getattr(stat_result, "st_mtime_ns", 0) or 0)
        payload = {
            "engine": engine_signature,
            "state": state,
            "id": str(game.get("id") or ""),
            "core": str(game.get("core") or ""),
            "platform": str(game.get("platform") or ""),
            "game_code": str(game.get("game_code") or ""),
            "needed_bios": _normalize_required_archive(game.get("needed_bios") or ""),
            "bios_state": str(bios_state or ""),
            "bundle_state": str(bundle_state or ""),
            "db_path": str(game.get("file_path") or ""),
            "resolved_path": os.path.realpath(resolved_path) if resolved_path else "",
            "size": stat_size,
            "mtime_ns": stat_mtime_ns,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _health_bios_cache_state(game, available_bios_names, bios_dir, state_cache=None):
        """해당 게임에 영향을 주는 BIOS 상태만 캐시 키에 반영하고 동일 BIOS 검사는 재사용한다."""
        required = _normalize_required_archive(game.get("needed_bios") or "")
        if not required and str(game.get("health_status") or "") == "bios_required":
            required = _normalize_required_archive(game.get("missing_roms") or "")
        if not required:
            return ""
        cache = state_cache if isinstance(state_cache, dict) else None
        if cache is not None and required in cache:
            return cache[required]
        available = _is_required_bios_available(required, available_bios_names, bios_dir)
        value = f"{required}:{1 if available else 0}"
        if cache is not None:
            cache[required] = value
        return value

    @staticmethod
    def _health_bundle_cache_state(game, resolved_path, state_cache=None):
        """M3U/CUE/GDI 및 Arcade CHD 같은 보조 파일 추가·삭제를 빠르게 감지한다."""
        path = str(resolved_path or "")
        if not path:
            return ""
        ext = os.path.splitext(path)[1].lower()
        core = _health_core_key(game.get("core"))
        platform = _health_core_key(game.get("platform"))
        needs_bundle_state = ext in {".m3u", ".cue", ".gdi"} or core in {
            "arcade", "mame", "mame2003", "mame2003plus"
        } or platform in {"arcade", "neogeo"}
        if not needs_bundle_state:
            return ""
        parent = os.path.realpath(os.path.dirname(path))
        cache = state_cache if isinstance(state_cache, dict) else None
        if cache is not None and parent in cache:
            return cache[parent]
        try:
            st = os.stat(parent)
            value = f"{parent}:{int(st.st_mtime_ns)}"
        except OSError:
            value = f"{parent}:missing"
        if cache is not None:
            cache[parent] = value
        return value

    @staticmethod
    def _cached_analysis_snapshot(game):
        raw = str((game or {}).get("analysis_json") or "")
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _analysis_environment(self):
        bios_dir = self._get_bios_dir()
        try:
            available_bios_names = {
                name.lower() for name in os.listdir(bios_dir) if not name.startswith(".")
            } if os.path.isdir(bios_dir) else set()
        except Exception:
            available_bios_names = set()
        return bios_dir, available_bios_names, self._health_engine_signature(available_bios_names)

    def _evaluate_rom_analysis(
        self, game, *, available_bios_names=None, bios_dir=None, engine_signature=None,
        bios_state_cache=None, bundle_state_cache=None, force=False,
    ):
        """개별 ROM의 상세 분석과 health 요약을 한 번에 계산한다.

        이 함수가 상세보기와 전체 분석 갱신의 공통 원본이다. DB 경로는 절대 수정하지 않는다.
        """
        game = dict(game or {})
        gid = str(game.get("id") or "")
        filename = str(game.get("filename") or "")
        if bios_dir is None or available_bios_names is None or engine_signature is None:
            env_bios_dir, env_bios_names, env_signature = self._analysis_environment()
            bios_dir = env_bios_dir if bios_dir is None else bios_dir
            available_bios_names = env_bios_names if available_bios_names is None else available_bios_names
            engine_signature = env_signature if engine_signature is None else engine_signature

        db_path = str(game.get("file_path") or "")
        file_path = db_path if db_path and os.path.isfile(db_path) else ""
        file_state = "ok"
        if not file_path:
            resolved = self._resolve_existing_rom_path(
                gid, filename, db_path,
                core=game.get("core") or "", platform=game.get("platform") or "", update_db=False,
            )
            if resolved and os.path.isfile(resolved):
                file_path = resolved
                file_state = "path_mismatch"
            else:
                file_state = "missing_file"

        stat_result = None
        if file_path:
            try:
                stat_result = os.stat(file_path)
            except OSError:
                file_path = ""
                file_state = "missing_file"

        bios_state = self._health_bios_cache_state(
            game, available_bios_names, bios_dir, state_cache=bios_state_cache
        )
        bundle_state = self._health_bundle_cache_state(
            game, file_path, state_cache=bundle_state_cache
        )
        cache_key = self._health_cache_key(
            game, engine_signature, file_state, file_path, stat_result,
            bios_state=bios_state, bundle_state=bundle_state,
        )
        cached_snapshot = self._cached_analysis_snapshot(game)
        cache_reused = bool(
            not force and cached_snapshot
            and str(game.get("health_cache_key") or "") == cache_key
            and str(game.get("analysis_cache_key") or "") == cache_key
        )
        if cache_reused:
            return {
                "kind": "cached", "game_id": gid, "target": None, "snapshot": cached_snapshot,
                "file_path": file_path, "file_state": file_state, "cache_key": cache_key,
                "cache_reused": True, "error": "",
            }

        now_str = _get_kst_now_str()
        if file_state == "missing_file":
            status = "missing_file"
            reason = "DB에 등록된 ROM 파일을 현재 경로 또는 관리 저장소에서 찾을 수 없습니다."
            snapshot = _build_analysis_snapshot(
                cached_snapshot or {}, status, reason, file_state, cache_key, now_str
            )
            target = {
                "status": status, "reason": reason,
                "analysis_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                "analysis_cache_key": cache_key,
                "content_identity_key": str(game.get("content_identity_key") or self._content_identity_key(game, snapshot)),
                "metadata_source": str(snapshot.get("metadata_source") or game.get("metadata_source") or ""),
                "metadata_confidence": int(snapshot.get("metadata_confidence") or game.get("metadata_confidence") or 0),
                "source_system": str(snapshot.get("source_system") or game.get("source_system") or ""),
                "needed_bios": _normalize_required_archive(snapshot.get("needed_bios") or game.get("needed_bios") or ""),
                "cache_key": cache_key,
            }
            return {
                "kind": "update", "game_id": gid, "target": target, "snapshot": snapshot,
                "file_path": "", "file_state": file_state, "cache_key": cache_key,
                "cache_reused": False, "error": "",
            }

        try:
            from rom_analysis_adapter import analyze_rom
            fresh = analyze_rom(file_path)
            if not isinstance(fresh, dict):
                raise RuntimeError("rom-analyzer 결과 형식이 올바르지 않습니다.")
            derived_status, derived_reason = _derive_health_status_from_analysis(
                fresh, file_path,
                db_core=game.get("core") or "", db_platform=game.get("platform") or "",
                db_game_code=game.get("game_code") or "",
                available_bios_names=available_bios_names, bios_dir=bios_dir,
            )
            status = str(derived_status or "unverified")
            reason = str(derived_reason or "")
            if file_state == "path_mismatch":
                status = "path_mismatch"
                reason = "DB의 ROM 경로와 실제 발견 위치가 다릅니다. 라이브러리 동기화로 경로를 갱신하세요."

            detected_needed_bios = _normalize_required_archive(fresh.get("needed_bios") or "")
            cache_game = dict(game)
            cache_game["needed_bios"] = detected_needed_bios
            detected_bios_state = self._health_bios_cache_state(
                cache_game, available_bios_names, bios_dir, state_cache=bios_state_cache
            )
            final_cache_key = self._health_cache_key(
                cache_game, engine_signature, file_state, file_path, stat_result,
                bios_state=detected_bios_state, bundle_state=bundle_state,
            )
            snapshot = _build_analysis_snapshot(
                fresh, status, reason, file_state, final_cache_key, now_str
            )
            content_identity_key = self._content_identity_key(cache_game, snapshot)
            target = {
                "status": status, "reason": reason,
                "analysis_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                "analysis_cache_key": final_cache_key,
                "content_identity_key": content_identity_key,
                "metadata_source": str(fresh.get("metadata_source") or "rom-analyzer"),
                "metadata_confidence": int(fresh.get("metadata_confidence") or 0),
                "source_system": str(fresh.get("source_system") or "rom_analyzer"),
                "needed_bios": detected_needed_bios,
                "cache_key": final_cache_key,
            }
            return {
                "kind": "update", "game_id": gid, "target": target, "snapshot": snapshot,
                "file_path": file_path, "file_state": file_state, "cache_key": final_cache_key,
                "cache_reused": False, "error": "",
            }
        except Exception as exc:
            # 경로 불일치는 analyzer가 일시적으로 실패해도 현재 storage 상태 자체는 확정할 수 있다.
            if file_state == "path_mismatch":
                status = "path_mismatch"
                reason = "DB의 ROM 경로와 실제 발견 위치가 다릅니다. 라이브러리 동기화로 경로를 갱신하세요."
                snapshot = _build_analysis_snapshot(
                    cached_snapshot or {}, status, reason, file_state, cache_key, now_str
                )
                target = {
                    "status": status, "reason": reason,
                    "analysis_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    "analysis_cache_key": cache_key,
                    "content_identity_key": str(game.get("content_identity_key") or self._content_identity_key(game, snapshot)),
                    "metadata_source": str(snapshot.get("metadata_source") or game.get("metadata_source") or ""),
                    "metadata_confidence": int(snapshot.get("metadata_confidence") or game.get("metadata_confidence") or 0),
                    "source_system": str(snapshot.get("source_system") or game.get("source_system") or ""),
                    "needed_bios": _normalize_required_archive(snapshot.get("needed_bios") or game.get("needed_bios") or ""),
                    "cache_key": cache_key,
                }
                return {
                    "kind": "update", "game_id": gid, "target": target, "snapshot": snapshot,
                    "file_path": file_path, "file_state": file_state, "cache_key": cache_key,
                    "cache_reused": False, "error": str(exc),
                }
            return {
                "kind": "failed", "game_id": gid, "target": None,
                "snapshot": cached_snapshot or {}, "file_path": file_path,
                "file_state": file_state, "cache_key": cache_key,
                "cache_reused": False, "error": str(exc),
            }

    @staticmethod
    def _analysis_update_params(target, game_id):
        return (
            target["status"], target["reason"], target["metadata_source"],
            target["metadata_confidence"], target["source_system"], target["needed_bios"],
            target["cache_key"], target["analysis_json"], target["analysis_cache_key"],
            target["content_identity_key"], game_id,
        )

    def _persist_rom_analysis(self, game_id, target):
        self._db_execute(
            "UPDATE games SET health_status=?, missing_roms=?, metadata_source=?, "
            "metadata_confidence=?, source_system=?, needed_bios=?, health_cache_key=?, "
            "analysis_json=?, analysis_cache_key=?, content_identity_key=? WHERE id=?",
            self._analysis_update_params(target, game_id),
        )

    def _refresh_rom_analyses(self, force=False):
        """모든 ROM의 단일 분석 원본을 증분 갱신한다."""
        with _HEALTH_PROGRESS_LOCK:
            if _HEALTH_PROGRESS.get("is_running"):
                return
            _HEALTH_PROGRESS.update({
                "is_running": True, "current": 0, "total": 0, "current_file": "",
                "status": "preparing", "changed": 0, "cached": 0, "failed": 0, "updated_at": time.time(),
            })

        try:
            rows = self._db_query(
                "SELECT id, filename, file_path, core, platform, game_code, needed_bios, health_status, missing_roms, size_bytes, "
                "rom_crc32, rom_md5, rom_sha1, serial_code, normalized_title, "
                "metadata_source, metadata_confidence, source_system, COALESCE(health_cache_key, '') AS health_cache_key, "
                "COALESCE(analysis_json, '') AS analysis_json, COALESCE(analysis_cache_key, '') AS analysis_cache_key, "
                "COALESCE(content_identity_key, '') AS content_identity_key "
                "FROM games WHERE COALESCE(deletion_status, 'active') = 'active' ORDER BY id"
            )
            total = len(rows)
            bios_dir, available_bios_names, engine_signature = self._analysis_environment()
            bios_state_cache = {}
            bundle_state_cache = {}
            _update_health_progress(
                current=0, total=total, current_file="ROM 분석 준비 중...", status="analyzing",
                is_running=True, changed=0, cached=0, failed=0,
            )
            counters = {"current": 0, "cached": 0, "failed": 0}
            counter_lock = threading.Lock()

            def finish_progress(filename, kind):
                with counter_lock:
                    counters["current"] += 1
                    if kind == "cached":
                        counters["cached"] += 1
                    elif kind == "failed":
                        counters["failed"] += 1
                    _update_health_progress(
                        current=counters["current"], total=total, current_file=filename,
                        status="analyzing", is_running=True, cached=counters["cached"], failed=counters["failed"],
                    )

            def analyze_one(game):
                result = self._evaluate_rom_analysis(
                    game,
                    available_bios_names=available_bios_names,
                    bios_dir=bios_dir,
                    engine_signature=engine_signature,
                    bios_state_cache=bios_state_cache,
                    bundle_state_cache=bundle_state_cache,
                    force=force,
                )
                finish_progress(game.get("filename") or "", result.get("kind") or "failed")
                return result

            results = []
            if rows:
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(analyze_one, row) for row in rows]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            with counter_lock:
                                counters["failed"] += 1
                            logger.debug(f"[{SELF_ID}] ROM analysis worker error: {exc}")

            old_by_id = {row.get("id"): row for row in rows}
            updates = []
            changed = 0
            for result in results:
                if result.get("kind") != "update" or not result.get("target"):
                    continue
                gid = result.get("game_id")
                target = result["target"]
                old = old_by_id.get(gid) or {}
                if (
                    str(old.get("health_status") or "pass") != target["status"]
                    or str(old.get("missing_roms") or "") != target["reason"]
                ):
                    changed += 1
                old_values = (
                    str(old.get("health_status") or "pass"), str(old.get("missing_roms") or ""),
                    str(old.get("metadata_source") or ""), int(old.get("metadata_confidence") or 0),
                    str(old.get("source_system") or ""), str(old.get("needed_bios") or ""),
                    str(old.get("health_cache_key") or ""), str(old.get("analysis_json") or ""),
                    str(old.get("analysis_cache_key") or ""), str(old.get("content_identity_key") or ""),
                )
                new_values = self._analysis_update_params(target, gid)[:-1]
                if old_values != new_values:
                    updates.append(self._analysis_update_params(target, gid))

            if updates:
                with _DB_LOCK:
                    conn = self._get_db_conn(timeout=60)
                    try:
                        conn.executemany(
                            "UPDATE games SET health_status=?, missing_roms=?, metadata_source=?, "
                            "metadata_confidence=?, source_system=?, needed_bios=?, health_cache_key=?, "
                            "analysis_json=?, analysis_cache_key=?, content_identity_key=? WHERE id=?",
                            updates,
                        )
                        conn.commit()
                    finally:
                        conn.close()

            _update_health_progress(
                current=total, total=total, current_file="완료", status="completed", is_running=False,
                changed=changed, cached=counters["cached"], failed=counters["failed"],
            )
        except Exception as exc:
            logger.exception(f"[{SELF_ID}] ROM analysis refresh error: {exc}")
            _update_health_progress(status="error", is_running=False, failed=1, current_file=str(exc))

    def _refresh_health_statuses(self):
        """기존 내부 호출 호환용. 실제 작업은 단일 ROM 분석 갱신으로 통합됐다."""
        return self._refresh_rom_analyses(force=False)

    @staticmethod
    def _relocation_identity_matches(old_game, identity_info):
        """유일한 파일명+크기 이동 후보가 실제 같은 ROM인지 저장된 해시로 추가 검증한다."""
        old_game = old_game or {}
        identity_info = identity_info or {}
        for key in ("rom_sha1", "rom_md5", "rom_crc32"):
            old_hash = str(old_game.get(key) or "").strip().lower()
            new_hash = str(identity_info.get(key) or "").strip().lower()
            if old_hash and new_hash:
                return old_hash == new_hash
        return True

    def _merge_game_user_state(self, old_game_id, new_game_id):
        """ROM 경로/기종 재배치로 game_id가 바뀔 때 유저 기록과 세이브를 보존한다."""
        old_game_id = str(old_game_id or "").strip()
        new_game_id = str(new_game_id or "").strip()
        if not old_game_id or not new_game_id or old_game_id == new_game_id:
            return True

        try:
            rows = self._db_query(
                "SELECT user_id, is_favorite, last_played_at, play_count FROM user_game_data WHERE game_id = ?",
                (old_game_id,),
            )
            for row in rows:
                user_id = int(row.get("user_id") or 0)
                target_rows = self._db_query(
                    "SELECT is_favorite, last_played_at, play_count FROM user_game_data WHERE user_id = ? AND game_id = ?",
                    (user_id, new_game_id),
                )
                if target_rows:
                    target = target_rows[0]
                    favorite = max(int(row.get("is_favorite") or 0), int(target.get("is_favorite") or 0))
                    play_count = max(int(row.get("play_count") or 0), int(target.get("play_count") or 0))
                    last_old = str(row.get("last_played_at") or "")
                    last_new = str(target.get("last_played_at") or "")
                    last_played = max(last_old, last_new) if last_old and last_new else (last_old or last_new)
                    self._db_execute(
                        "UPDATE user_game_data SET is_favorite = ?, last_played_at = ?, play_count = ? WHERE user_id = ? AND game_id = ?",
                        (favorite, last_played, play_count, user_id, new_game_id),
                    )
                    self._db_execute(
                        "DELETE FROM user_game_data WHERE user_id = ? AND game_id = ?",
                        (user_id, old_game_id),
                    )
                else:
                    self._db_execute(
                        "UPDATE user_game_data SET game_id = ? WHERE user_id = ? AND game_id = ?",
                        (new_game_id, user_id, old_game_id),
                    )

                # 세이브/상태 파일도 game_id를 파일명으로 사용하므로 같은 시점에 함께 이동한다.
                try:
                    saves_dir = self._get_user_saves_dir(user_id)
                    suffixes = [".sav", ".state", "_slot1.state", "_slot2.state", "_slot3.state"]
                    for suffix in suffixes:
                        src = os.path.join(saves_dir, f"{old_game_id}{suffix}")
                        dst = os.path.join(saves_dir, f"{new_game_id}{suffix}")
                        if not os.path.isfile(src):
                            continue
                        if not os.path.exists(dst):
                            os.replace(src, dst)
                        elif os.path.getmtime(src) > os.path.getmtime(dst):
                            os.replace(src, dst)
                        else:
                            os.remove(src)
                except Exception as exc:
                    logger.warning(f"[{SELF_ID}] Save migration failed ({old_game_id} -> {new_game_id}): {exc}")
            return True
        except Exception as exc:
            logger.warning(f"[{SELF_ID}] User state migration failed ({old_game_id} -> {new_game_id}): {exc}")
            return False

    def _run_library_sync(self, mode="sync"):
        """Game Books 라이브러리 처리의 공통 진입점.

        - ingest: 업로드/외부 유입 후 신규·변경 ROM만 동기화
        - sync: 일반 라이브러리 동기화
        - rebuild: 모든 ROM을 최신 분석 기준으로 강제 재분석
        - diagnose: 파일을 변경하지 않고 전체 ROM 분석 원본과 health 요약을 함께 갱신
        """
        mode = str(mode or "sync").strip().lower()
        if mode not in {"ingest", "sync", "rebuild", "diagnose"}:
            raise ValueError(f"지원하지 않는 라이브러리 동기화 모드입니다: {mode}")

        if not _LIBRARY_SYNC_LOCK.acquire(blocking=False):
            return {
                "success": False,
                "busy": True,
                "mode": mode,
                "error": "다른 라이브러리 작업이 진행 중입니다.",
            }

        try:
            if mode == "ingest":
                return self._scan_roms(new_only=True)
            if mode in {"sync", "rebuild"}:
                deletion_stats = self._process_pending_deletions()
                scan_stats = self._scan_roms(force_full=(mode == "rebuild"))
                if isinstance(scan_stats, dict):
                    scan_stats.update(deletion_stats)
                return scan_stats
            self._refresh_rom_analyses(force=False)
            return {"success": True, "mode": mode}
        finally:
            _LIBRARY_SYNC_LOCK.release()

    def _run_library_sync_background(self, mode):
        """백그라운드 공통 엔진 실행. 잠금 경합 시 queued 상태가 남지 않게 정리한다."""
        result = self._run_library_sync(mode)
        if isinstance(result, dict) and result.get("busy"):
            logger.warning(f"[{SELF_ID}] Library sync background busy ({mode})")
            if mode == "diagnose":
                _update_health_progress(
                    status="error",
                    is_running=False,
                    failed=1,
                    current_file=result.get("error") or "다른 라이브러리 작업이 진행 중입니다.",
                )
        return result

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

        allowed_exts = set(SUPPORTED_SYSTEMS.keys()) | {".zip", ".7z", ".cue", ".gdi", ".chd", ".m3u"}
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

        # M3U는 멀티디스크 게임의 대표 실행 파일이다. 플레이리스트가 참조한 디스크는
        # 독립 게임 카드로 중복 등록하지 않고 M3U의 sidecar로만 유지한다.
        found_files, claimed_disk_paths = _filter_m3u_claimed_files(found_files)

        # 삭제 예약/처리 실패 행은 물리 파일이 아직 남아 있어도 다시 신규 게임으로 등록하지 않는다.
        tombstones = self._db_query(
            "SELECT id, filename, file_path, size_bytes, deletion_status FROM games "
            "WHERE COALESCE(deletion_status, 'active') != 'active'"
        )
        tombstone_ids = {str(row.get("id") or "") for row in tombstones if row.get("id")}
        tombstone_paths = {
            os.path.realpath(str(row.get("file_path") or ""))
            for row in tombstones if str(row.get("file_path") or "").strip()
        }
        found_files = {
            gid: info for gid, info in found_files.items()
            if gid not in tombstone_ids
            and os.path.realpath(str(info.get("file_path") or "")) not in tombstone_paths
        }

        existing_games = {
            g["id"]: g for g in self._db_query(
                "SELECT * FROM games WHERE COALESCE(deletion_status, 'active') = 'active'"
            )
        }

        # 전체 재구축은 기존 진단 캐시를 신뢰하지 않는다. 먼저 무효화한 뒤 이번 전수 분석에
        # 성공한 게임만 새 health_cache_key를 저장한다. 중간 실패/중단 항목은 빈 키로 남아
        # 다음 전체 ROM 분석 갱신에서 자동 재분석된다.
        if force_full:
            self._db_execute(
                "UPDATE games SET health_cache_key = '' "
                "WHERE COALESCE(deletion_status, 'active') = 'active' AND COALESCE(health_cache_key, '') != ''"
            )

        # 외부에서 ROM을 다른 폴더로 옮긴 경우 경로 기반 game_id가 바뀐다.
        # 파일명+크기가 같은 이전 항목이 유일할 때만 동일 게임의 이동으로 간주한다.
        missing_by_signature = {}
        for old_gid, old_game in existing_games.items():
            if old_gid in found_files:
                continue
            signature = (
                str(old_game.get("filename") or "").lower(),
                int(old_game.get("size_bytes") or 0),
            )
            missing_by_signature.setdefault(signature, []).append(old_gid)

        relocation_sources = {}
        for found_gid, found_info in found_files.items():
            if found_gid in existing_games:
                continue
            signature = (
                str(found_info.get("filename") or "").lower(),
                int(found_info.get("size_bytes") or 0),
            )
            candidates = missing_by_signature.get(signature) or []
            if len(candidates) == 1:
                relocation_sources[found_gid] = candidates[0]

        now_str = _get_kst_now_str()
        available_rom_names = {str(v.get("filename") or "").lower() for v in found_files.values() if v.get("filename")}
        available_bios_names = set()
        bios_dir = self._get_bios_dir()
        if os.path.isdir(bios_dir):
            try:
                available_bios_names = {f.lower() for f in os.listdir(bios_dir) if not f.startswith(".")}
            except Exception:
                available_bios_names = set()

        health_engine_signature = self._health_engine_signature(available_bios_names)
        health_bios_state_cache = {}
        health_bundle_state_cache = {}

        def _build_scan_health_cache_key(gid, file_path, rom_info, health_status, missing_roms):
            """스캔에서 이미 얻은 분석 결과를 다음 전체 ROM 분석 갱신 캐시로 재사용한다."""
            try:
                stat_result = os.stat(file_path)
            except OSError:
                return ""
            cache_game = {
                "id": str(gid or ""),
                "file_path": str(file_path or ""),
                "core": str((rom_info or {}).get("core") or ""),
                "platform": str((rom_info or {}).get("platform") or ""),
                "game_code": str((rom_info or {}).get("game_code") or ""),
                "needed_bios": _normalize_required_archive((rom_info or {}).get("needed_bios") or ""),
                "health_status": str(health_status or ""),
                "missing_roms": str(missing_roms or ""),
            }
            bios_state = self._health_bios_cache_state(
                cache_game, available_bios_names, bios_dir, state_cache=health_bios_state_cache
            )
            bundle_state = self._health_bundle_cache_state(
                cache_game, file_path, state_cache=health_bundle_state_cache
            )
            return self._health_cache_key(
                cache_game, health_engine_signature, "ok", file_path, stat_result,
                bios_state=bios_state, bundle_state=bundle_state,
            )

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
                analysis_result, rom_info = _analyze_rom_context(info["file_path"])
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

                source_gid = relocation_sources.get(gid) or gid
                if source_gid != gid and not self._relocation_identity_matches(existing_games.get(source_gid) or {}, identity_info):
                    source_gid = gid

                return {
                    "gid": gid,
                    "source_gid": source_gid,
                    "info": info,
                    "rom_info": rom_info,
                    "analysis_result": analysis_result,
                    "clean_title": clean_title,
                    "mapped_header": mapped_header,
                    "identity_info": identity_info,
                }
            except Exception as ex:
                logger.debug(f"[{SELF_ID}] Process single rom error ({info.get('filename')}): {ex}")
                return None

        # ThreadPoolExecutor를 이용한 멀티스레드 병렬 바이너리 분석
        failed_relocations = set()
        existing_update_sql = (
            "UPDATE games SET future_id = ?, file_path = ?, size_bytes = ?, mtime = ?, core = ?, platform = ?, "
            "title = ?, game_code = ?, needed_bios = ?, health_status = ?, missing_roms = ?, rom_crc32 = ?, "
            "rom_md5 = ?, rom_sha1 = ?, serial_code = ?, normalized_title = ?, source_system = ?, "
            "metadata_source = COALESCE(NULLIF(metadata_source, ''), ?), "
            "metadata_confidence = CASE WHEN metadata_confidence IS NULL OR metadata_confidence = 0 THEN ? ELSE metadata_confidence END, "
            "region_tag = ?, revision_tag = ?, disc_number = ?, content_flags = ?, health_cache_key = ?, "
            "analysis_json = ?, analysis_cache_key = ?, content_identity_key = ?, cover_path = COALESCE(cover_path, ?) WHERE id = ?"
        )
        pending_existing_updates = []
        pending_cover_updates = []

        def _flush_existing_update_batch():
            if not pending_existing_updates and not pending_cover_updates:
                return
            self._db_execute_batches([
                (existing_update_sql, pending_existing_updates),
                ("UPDATE games SET cover_path = ? WHERE id = ?", pending_cover_updates),
            ])
            pending_existing_updates.clear()
            pending_cover_updates.clear()

        if files_to_process:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(_process_single_rom, files_to_process))

            results = [res for res in results if res]
            saving_total = len(results)
            _update_scan_progress(
                current=0,
                total=saving_total,
                current_file="분석 결과 반영 준비 중...",
                status="saving",
                is_running=True,
            )

            covers_dir = self._get_covers_dir()

            for saving_index, res in enumerate(results, start=1):
                gid = res["gid"]
                source_gid = res.get("source_gid") or gid
                info = res["info"]
                rom_info = res["rom_info"]
                analysis_result = res.get("analysis_result")
                clean_title = res["clean_title"]
                mapped_header = res["mapped_header"]
                identity_info = res.get("identity_info") or {}

                curr_file_path = info["file_path"]
                curr_dir = os.path.dirname(os.path.abspath(curr_file_path))
                scan_root = os.path.abspath(info.get("sdir") or curr_dir)

                target_core_folder = (rom_info.get("core") or rom_info.get("platform") or "other").lower()
                target_core_folder = re.sub(r"[^a-zA-Z0-9_\-]", "_", target_core_folder).strip() or "other"
                current_folder_name = os.path.basename(curr_dir).lower()

                # 모든 .7z 압축 롬 파일: 브라우저 EmulatorJS 호환성 및 안정적 구동을 위해 표준 .zip으로 영구 자동 변환
                f_ext = os.path.splitext(info["filename"])[1].lower()
                if f_ext == ".7z":
                    try:
                        import py7zr
                        zip_fname = os.path.splitext(info["filename"])[0] + ".zip"
                        ideal_dir = os.path.join(scan_root, target_core_folder)
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

                        sdir = info.get("sdir") or scan_root
                        rel = os.path.relpath(dest_zip_path, sdir)
                        new_gid = _sanitize_id(f"{os.path.basename(sdir)}_{rel}")
                        if gid in found_files:
                            del found_files[gid]
                        found_files[new_gid] = info
                        gid = new_gid
                    except Exception as conv_ex:
                        logger.error(f"[{SELF_ID}] Scan 7z convert error: {conv_ex}")
                elif f_ext in (".cue", ".gdi", ".m3u") or current_folder_name != target_core_folder:
                    try:
                        ideal_dir = os.path.join(scan_root, target_core_folder)
                        os.makedirs(ideal_dir, exist_ok=True)
                        original_path = curr_file_path
                        move_result = _move_disk_bundle(curr_file_path, ideal_dir, related_infos=found_files.values())
                        dest_file_path = move_result.get("primary_path") or original_path
                        if move_result.get("moved") and dest_file_path != original_path:
                            curr_file_path = dest_file_path
                            info["file_path"] = dest_file_path
                            info["filename"] = os.path.basename(dest_file_path)

                            sdir = info.get("sdir") or scan_root
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

                # ROM 분석 요약: 최신 rom-analyzer의 명시적 근거만 사용한다.
                health_source = rom_info if rom_info.get("metadata_source") == "rom-analyzer" else {}
                health_status, missing_roms_str = _derive_health_status_from_analysis(
                    health_source, curr_file_path,
                    db_core=rom_info.get("core") or "", db_platform=rom_info.get("platform") or "",
                    available_bios_names=available_bios_names, bios_dir=bios_dir,
                )
                health_cache_key = _build_scan_health_cache_key(
                    gid, curr_file_path, rom_info, health_status, missing_roms_str
                )
                analysis_snapshot = _build_analysis_snapshot(
                    rom_info, health_status, missing_roms_str, "ok", health_cache_key, _get_kst_now_str()
                )
                analysis_json = json.dumps(analysis_snapshot, ensure_ascii=False, sort_keys=True)
                analysis_cache_game = {
                    "id": gid,
                    "core": rom_info.get("core") or "",
                    "platform": rom_info.get("platform") or "",
                    "game_code": rom_info.get("game_code") or "",
                    "health_cache_key": health_cache_key,
                }
                analysis_cache_key = self._analysis_detail_cache_key(
                    analysis_cache_game, curr_file_path, health_cache_key=health_cache_key
                )
                content_identity_game = dict(identity_info)
                content_identity_game.update({
                    "filename": info.get("filename") or "",
                    "size_bytes": info.get("size_bytes") or 0,
                    "core": rom_info.get("core") or "",
                    "platform": rom_info.get("platform") or "",
                    "game_code": rom_info.get("game_code") or "",
                })
                content_identity_key = self._content_identity_key(content_identity_game, analysis_snapshot)

                existing_cover_file = self._resolve_existing_cover(
                    gid,
                    info["filename"],
                    rom_info.get("core") or rom_info.get("platform"),
                    current_cover_path=((existing_games.get(gid) or existing_games.get(source_gid) or {}).get("cover_path") or ""),
                    update_db=False,
                )

                # 최종 legacy gid가 확정된 뒤에만 영구 INTEGER ID를 예약한다.
                # 기존 게임의 relocation이면 새 번호를 만들지 않고 기존 future_id를 그대로 승계한다.
                if source_gid != gid and source_gid in existing_games:
                    future_id = int((existing_games.get(source_gid) or {}).get("future_id") or 0)
                    if future_id <= 0:
                        future_id = self._get_or_create_future_game_id(source_gid)
                elif gid in existing_games:
                    # Phase 5에서 이미 영구 ID가 백필된 기존 게임은 매번 game_id_map을
                    # 다시 열어 조회하지 않는다. 누락된 구버전 데이터만 느린 생성 경로를 탄다.
                    future_id = int((existing_games.get(gid) or {}).get("future_id") or 0)
                    if future_id <= 0:
                        future_id = self._get_or_create_future_game_id(gid)
                else:
                    future_id = self._get_or_create_future_game_id(gid)

                is_new_ingest = bool(
                    new_only
                    and gid not in existing_games
                    and source_gid not in existing_games
                )

                try:
                    if gid not in existing_games:
                        self._db_execute(
                            """INSERT OR REPLACE INTO games (id, future_id, filename, file_path, title, game_code, maker_code, core, platform, size_bytes, mtime, added_at, cover_path, needed_bios, health_status, missing_roms, rom_crc32, rom_md5, rom_sha1, serial_code, normalized_title, source_system, metadata_source, metadata_confidence, region_tag, revision_tag, disc_number, content_flags, health_cache_key, analysis_json, analysis_cache_key, content_identity_key)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                gid,
                                future_id,
                                info["filename"],
                                curr_file_path,
                                clean_title,
                                rom_info["game_code"],
                                rom_info["maker_code"],
                                rom_info["core"],
                                rom_info["platform"],
                                info["size_bytes"],
                                info["mtime"],
                                (existing_games.get(source_gid) or {}).get("added_at") or now_str,
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
                                health_cache_key,
                                analysis_json,
                                analysis_cache_key,
                                content_identity_key,
                            ),
                        )

                        if is_new_ingest and analysis_result is not None:
                            try:
                                placement = self._place_new_ingest_content(
                                    analysis_result,
                                    future_id,
                                    curr_file_path,
                                )
                                if placement and placement.success and placement.rom_dest_path:
                                    curr_file_path = os.path.abspath(placement.rom_dest_path)
                                    info["file_path"] = curr_file_path
                                    info["filename"] = os.path.basename(curr_file_path)
                                    info["size_bytes"] = os.path.getsize(curr_file_path)
                                    info["mtime"] = os.path.getmtime(curr_file_path)
                                    health_cache_key = _build_scan_health_cache_key(
                                        gid, curr_file_path, rom_info, health_status, missing_roms_str
                                    )
                                    analysis_cache_key = self._analysis_detail_cache_key(
                                        analysis_cache_game, curr_file_path, health_cache_key=health_cache_key
                                    )
                                    analysis_snapshot = _build_analysis_snapshot(
                                        rom_info, health_status, missing_roms_str, "ok", health_cache_key, _get_kst_now_str()
                                    )
                                    analysis_json = json.dumps(analysis_snapshot, ensure_ascii=False, sort_keys=True)
                                    self._db_execute(
                                        """UPDATE games
                                           SET layout_version = 2, filename = ?, file_path = ?, size_bytes = ?, mtime = ?,
                                               health_cache_key = ?, analysis_json = ?, analysis_cache_key = ?
                                           WHERE id = ?""",
                                        (
                                            info["filename"],
                                            curr_file_path,
                                            info["size_bytes"],
                                            info["mtime"],
                                            health_cache_key,
                                            analysis_json,
                                            analysis_cache_key,
                                            gid,
                                        ),
                                    )
                                elif placement is not None:
                                    logger.warning(
                                        f"[{SELF_ID}] library_structures ingest placement failed "
                                        f"({info.get('filename')}): {'; '.join(placement.errors or [])}"
                                    )
                            except Exception as placement_exc:
                                logger.warning(
                                    f"[{SELF_ID}] library_structures ingest placement error "
                                    f"({info.get('filename')}): {placement_exc}"
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
                        # 일반 기존 게임 갱신은 매 항목마다 SQLite 연결/commit을 반복하지 않고
                        # 짧은 배치 트랜잭션으로 묶는다. 신규/relocation 경로는 즉시 반영을 유지한다.
                        pending_existing_updates.append((
                            future_id, curr_file_path, info["size_bytes"], info["mtime"],
                            rom_info["core"], rom_info["platform"], clean_title, rom_info["game_code"],
                            rom_info.get("needed_bios") or "", health_status, missing_roms_str,
                            identity_info.get("rom_crc32") or "", identity_info.get("rom_md5") or "",
                            identity_info.get("rom_sha1") or "", identity_info.get("serial_code") or "",
                            identity_info.get("normalized_title") or "", identity_info.get("source_system") or "",
                            identity_info.get("metadata_source") or "", identity_info.get("metadata_confidence") or 0,
                            identity_info.get("region_tag") or "", identity_info.get("revision_tag") or "",
                            identity_info.get("disc_number") or 0, identity_info.get("content_flags") or "",
                            health_cache_key, analysis_json, analysis_cache_key, content_identity_key, existing_cover_file, gid,
                        ))
                        existing_entry = existing_games.get(gid)
                        current_cover = existing_entry.get("cover_path") if existing_entry else None
                        if not current_cover or not os.path.exists(current_cover):
                            if existing_cover_file:
                                pending_cover_updates.append((existing_cover_file, gid))
                            elif not new_only:
                                covers_to_fetch.append((
                                    gid,
                                    rom_info.get("core") or rom_info.get("platform"),
                                    info["filename"],
                                    curr_file_path,
                                    mapped_header or clean_title
                                ))

                    if source_gid != gid and source_gid in existing_games:
                        if not self._merge_game_user_state(source_gid, gid):
                            failed_relocations.add(source_gid)
                        else:
                            try:
                                self._rebind_future_game_id(source_gid, gid, future_id)
                            except Exception:
                                failed_relocations.add(source_gid)
                                raise
                except Exception as dbe:
                    logger.warning(f"[{SELF_ID}] Game DB update error ({gid}): {dbe}")

                # 저장 단계는 분석 단계와 분리된 실제 항목 수로 진행률을 노출한다.
                _update_scan_progress(
                    current=saving_index,
                    total=saving_total,
                    current_file=info.get("filename") or clean_title or gid,
                    status="saving",
                    is_running=True,
                )

                # 지나치게 큰 단일 writer transaction을 피하면서 연결/commit 반복은 제거한다.
                if len(pending_existing_updates) >= 100:
                    _flush_existing_update_batch()

            _flush_existing_update_batch()

        # 삭제된 게임 정리. 신규/변경 파일이 하나도 없어도 디스크와 DB의 삭제 상태는 항상 맞춘다.
        # layout v2 게임은 legacy scan root 밖의 표준 library/에 있으므로 실제 파일이 살아 있으면 유지한다.
        structured_library_root = os.path.join(self._get_emulatorjs_root(), "library")
        for gid, existing_game in existing_games.items():
            if gid in failed_relocations:
                continue
            if gid not in found_files:
                layout_version = int((existing_game or {}).get("layout_version") or 1)
                structured_path = str((existing_game or {}).get("file_path") or "").strip()
                if (
                    layout_version >= 2
                    and structured_path
                    and os.path.isfile(structured_path)
                    and _path_within(structured_path, structured_library_root)
                ):
                    continue
                try:
                    self._db_execute("DELETE FROM games WHERE id = ?", (gid,))
                    self._db_execute("DELETE FROM user_game_data WHERE game_id = ?", (gid,))
                    self._delete_future_game_id(gid)
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

    def _request_game_deletion(self, game_id, requesting_user_id=0):
        """파일은 유지하고 게임을 삭제 예약 상태로 전환한다."""
        game_id = str(game_id or "").strip()
        if not game_id:
            return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}
        rows = self._db_query(
            "SELECT id, deletion_status FROM games WHERE id = ?",
            (game_id,),
        )
        if not rows:
            return {"success": False, "error": "삭제 예약할 게임을 찾을 수 없습니다."}

        status = str(rows[0].get("deletion_status") or "active").strip().lower()
        if status == "deleting":
            return {"success": False, "error": "현재 라이브러리 동기화에서 실제 삭제를 처리 중입니다."}

        requested_at = _get_kst_now_str()
        self._db_execute(
            "UPDATE games SET deletion_status = 'pending', deletion_requested_at = ?, "
            "deletion_requested_by = ?, deletion_error = '' WHERE id = ?",
            (requested_at, int(requesting_user_id or 0), game_id),
        )
        return {
            "success": True,
            "status": "pending",
            "message": "삭제 대기로 전환했습니다. 실제 파일은 다음 라이브러리 동기화 또는 전체 재구축에서 삭제됩니다.",
        }

    def _cancel_game_deletion(self, game_id):
        """아직 물리 삭제가 시작되지 않은 예약/실패 항목을 다시 활성화한다."""
        game_id = str(game_id or "").strip()
        if not game_id:
            return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}
        rows = self._db_query(
            "SELECT deletion_status FROM games WHERE id = ?",
            (game_id,),
        )
        if not rows:
            return {"success": False, "error": "게임을 찾을 수 없습니다."}
        status = str(rows[0].get("deletion_status") or "active").strip().lower()
        if status == "deleting":
            return {"success": False, "error": "실제 파일 삭제 처리 중에는 취소할 수 없습니다."}
        if status == "active":
            return {"success": True, "status": "active", "message": "이미 활성 상태입니다."}
        self._db_execute(
            "UPDATE games SET deletion_status = 'active', deletion_requested_at = '', "
            "deletion_requested_by = 0, deletion_error = '' WHERE id = ?",
            (game_id,),
        )
        return {"success": True, "status": "active", "message": "삭제 예약을 취소했습니다."}

    def _mark_game_deletion_failed(self, game_id, error):
        message = str(error or "실제 파일 삭제에 실패했습니다.")[:2000]
        try:
            self._db_execute(
                "UPDATE games SET deletion_status = 'failed', deletion_error = ? WHERE id = ?",
                (message, game_id),
            )
        except Exception:
            logger.exception(f"[{SELF_ID}] deletion failure state update failed ({game_id})")
        return message

    def _process_pending_deletions(self):
        """동기화/전체 재구축 시작 전에 삭제 예약 및 이전 실패 항목을 실제 삭제한다."""
        rows = self._db_query(
            "SELECT id, filename, deletion_status FROM games "
            "WHERE COALESCE(deletion_status, 'active') IN ('pending', 'deleting', 'failed') "
            "ORDER BY COALESCE(deletion_requested_at, ''), id"
        )
        total = len(rows)
        stats = {
            "delete_queue_count": total,
            "delete_processed_count": 0,
            "delete_failed_count": 0,
            "delete_preserved_shared_count": 0,
        }
        if not rows:
            return stats

        _update_scan_progress(
            current=0, total=total, current_file="삭제 예약 처리 준비 중...",
            status="deleting", is_running=True,
        )
        for index, row in enumerate(rows, start=1):
            game_id = str(row.get("id") or "")
            filename = str(row.get("filename") or game_id)
            _update_scan_progress(
                current=index - 1, total=total, current_file=filename,
                status="deleting", is_running=True,
            )
            result = self._delete_game_permanently(game_id)
            if result.get("success"):
                stats["delete_processed_count"] += 1
                stats["delete_preserved_shared_count"] += int(result.get("preserved_shared_count") or 0)
            else:
                stats["delete_failed_count"] += 1
            _update_scan_progress(
                current=index, total=total, current_file=filename,
                status="deleting", is_running=True,
            )
        return stats

    def _delete_game_permanently(self, game_id, requesting_user_id=0):
        """삭제 예약 항목의 ROM/번들/세이브/커버와 DB 레코드를 실제로 삭제한다.

        사용자 UI에서는 직접 호출하지 않고 라이브러리 동기화/전체 재구축에서만 호출한다.
        ROM은 같은 디렉터리의 숨김 임시 이름으로 먼저 원자적으로 옮긴 뒤 DB 삭제를
        커밋하고 실제 unlink한다. 실패 시 games 행을 failed tombstone으로 유지한다.
        """
        game_id = str(game_id or "").strip()
        if not game_id:
            return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}

        rows = self._db_query(
            "SELECT id, future_id, file_path, layout_version, cover_path, cover_thumbnail_path, "
            "COALESCE(deletion_status, 'active') AS deletion_status, COALESCE(deletion_requested_by, 0) AS deletion_requested_by "
            "FROM games WHERE id = ?",
            (game_id,),
        )
        if not rows:
            return {"success": True, "message": "이미 삭제된 게임입니다.", "deleted_rom_count": 0}
        game = rows[0]
        deletion_status = str(game.get("deletion_status") or "active").strip().lower()
        if deletion_status == "active":
            return {"success": False, "error": "삭제 예약되지 않은 게임은 물리 삭제할 수 없습니다."}

        self._db_execute(
            "UPDATE games SET deletion_status = 'deleting', deletion_error = '' WHERE id = ?",
            (game_id,),
        )

        primary_path = str(game.get("file_path") or "").strip()
        primary_abs = os.path.abspath(primary_path) if primary_path else ""
        candidates = []
        if primary_abs and os.path.exists(primary_abs):
            candidates = _collect_disk_bundle_paths(primary_abs) or [primary_abs]
            candidates = [os.path.abspath(path) for path in candidates if path and os.path.exists(path)]

        # 아직 DB에 남아 있는 다른 게임이 사용하는 primary/sidecar는 보호한다.
        protected = set()
        for other in self._db_query(
            "SELECT id, file_path FROM games WHERE id != ? AND file_path IS NOT NULL AND file_path != ''",
            (game_id,),
        ):
            other_path = str(other.get("file_path") or "").strip()
            if not other_path or not os.path.exists(other_path):
                continue
            other_abs = os.path.abspath(other_path)
            other_bundle = _collect_disk_bundle_paths(other_abs) or [other_abs]
            protected.update(os.path.realpath(path) for path in other_bundle if path and os.path.exists(path))

        primary_real = os.path.realpath(primary_abs) if primary_abs and os.path.exists(primary_abs) else ""
        if primary_real and primary_real in protected:
            error = self._mark_game_deletion_failed(
                game_id, "이 ROM 파일을 다른 게임 항목도 사용 중이어서 삭제할 수 없습니다."
            )
            return {"success": False, "error": error}

        delete_paths = []
        preserved_shared = []
        seen = set()
        for path in candidates:
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            if real in protected:
                preserved_shared.append(path)
                continue
            if not self._is_managed_storage_path(path):
                error = self._mark_game_deletion_failed(
                    game_id, f"관리 대상 저장소 밖의 파일은 삭제할 수 없습니다: {os.path.basename(path)}"
                )
                return {"success": False, "error": error}
            if os.path.isdir(path) and not os.path.islink(path):
                error = self._mark_game_deletion_failed(
                    game_id, "게임 파일 경로가 디렉터리여서 안전하게 삭제할 수 없습니다."
                )
                return {"success": False, "error": error}
            delete_paths.append(path)

        staged = []
        delete_tag = hashlib.sha256(
            f"{game_id}:{time.time_ns()}:{threading.get_ident()}".encode("utf-8")
        ).hexdigest()[:16]
        try:
            for index, original in enumerate(delete_paths):
                staged_path = os.path.join(
                    os.path.dirname(original),
                    f".bookoasis-delete-{delete_tag}-{index}-{os.path.basename(original)}",
                )
                if os.path.lexists(staged_path):
                    raise FileExistsError(staged_path)
                os.replace(original, staged_path)
                staged.append((original, staged_path))
        except Exception as exc:
            for original, staged_path in reversed(staged):
                try:
                    if os.path.lexists(staged_path) and not os.path.lexists(original):
                        os.replace(staged_path, original)
                except Exception:
                    logger.exception(f"[{SELF_ID}] delete rollback failed: {staged_path} -> {original}")
            error = self._mark_game_deletion_failed(game_id, f"ROM 파일 삭제 준비에 실패했습니다: {exc}")
            logger.warning(f"[{SELF_ID}] Game file delete staging failed ({game_id}): {exc}")
            return {"success": False, "error": error}

        save_user_ids = set()
        requested_by = int(game.get("deletion_requested_by") or requesting_user_id or 0)
        if requested_by > 0:
            save_user_ids.add(requested_by)
        try:
            for row in self._db_query("SELECT DISTINCT user_id FROM user_game_data WHERE game_id = ?", (game_id,)):
                uid = int(row.get("user_id") or 0)
                if uid > 0:
                    save_user_ids.add(uid)
        except Exception:
            pass

        try:
            with _DB_LOCK:
                conn = self._get_db_conn(timeout=60)
                try:
                    conn.execute("DELETE FROM user_game_data WHERE game_id = ?", (game_id,))
                    conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
                    conn.execute("DELETE FROM game_id_map WHERE legacy_id = ?", (game_id,))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
        except Exception as exc:
            for original, staged_path in reversed(staged):
                try:
                    if os.path.lexists(staged_path) and not os.path.lexists(original):
                        os.replace(staged_path, original)
                except Exception:
                    logger.exception(f"[{SELF_ID}] delete DB rollback file restore failed: {staged_path} -> {original}")
            error = self._mark_game_deletion_failed(game_id, f"DB 삭제에 실패했습니다: {exc}")
            logger.exception(f"[{SELF_ID}] Game DB delete failed ({game_id}): {exc}")
            return {"success": False, "error": error}

        deleted_rom_files = []
        cleanup_warnings = []
        for original, staged_path in staged:
            try:
                os.remove(staged_path)
                deleted_rom_files.append(os.path.basename(original))
            except Exception as exc:
                cleanup_warnings.append(os.path.basename(original))
                logger.error(f"[{SELF_ID}] staged ROM cleanup failed ({staged_path}): {exc}")

        if int(game.get("layout_version") or 1) >= 2 and primary_abs:
            try:
                game_dir = os.path.dirname(primary_abs)
                structured_root = os.path.join(self._get_emulatorjs_root(), "library")
                if _path_within(game_dir, structured_root) and not os.listdir(game_dir):
                    os.rmdir(game_dir)
            except Exception:
                pass

        deleted_save_files = []
        suffixes = (".sav", ".state", "_slot1.state", "_slot2.state", "_slot3.state")
        for uid in sorted(save_user_ids):
            try:
                user_saves_dir = self._get_user_saves_dir(uid)
            except Exception:
                continue
            for suffix in suffixes:
                save_path = os.path.join(user_saves_dir, f"{game_id}{suffix}")
                if not os.path.exists(save_path):
                    continue
                try:
                    if self._is_managed_storage_path(save_path):
                        os.remove(save_path)
                        deleted_save_files.append(f"{uid}:{os.path.basename(save_path)}")
                except Exception as exc:
                    logger.warning(f"[{SELF_ID}] Save delete failed ({save_path}): {exc}")

        deleted_cover_files = []
        for key in ("cover_path", "cover_large_path", "cover_thumbnail_path"):
            cover_path = str(game.get(key) or "").strip()
            if not cover_path or not os.path.exists(cover_path):
                continue
            try:
                if self._is_managed_storage_path(cover_path):
                    os.remove(cover_path)
                    deleted_cover_files.append(os.path.basename(cover_path))
            except Exception as exc:
                logger.warning(f"[{SELF_ID}] Cover delete failed ({cover_path}): {exc}")

        message = "삭제 예약 게임의 실제 ROM 파일과 라이브러리 데이터를 삭제했습니다."
        if not candidates:
            message = "ROM 파일은 이미 없었으며 삭제 예약된 라이브러리/세이브 데이터를 정리했습니다."
        if cleanup_warnings:
            message += " 일부 숨김 임시 파일 정리가 실패해 서버 로그 확인이 필요합니다."

        return {
            "success": True,
            "message": message,
            "deleted_rom_count": len(deleted_rom_files),
            "deleted_rom_files": deleted_rom_files,
            "preserved_shared_count": len(preserved_shared),
            "preserved_shared_files": [os.path.basename(path) for path in preserved_shared],
            "deleted_save_count": len(deleted_save_files),
            "deleted_cover_count": len(deleted_cover_files),
            "cleanup_warnings": cleanup_warnings,
        }

    def _get_db_schema_version(self):
        try:
            rows = self._db_query("SELECT version FROM schema_meta WHERE singleton = 1")
            return int(rows[0].get("version") or 0) if rows else 0
        except Exception:
            return 0

    def _create_phase6_db_backup(self):
        """WAL 상태를 포함한 일관된 SQLite backup API 백업을 생성한다."""
        source_path = self._get_db_path()
        backup_dir = os.path.join(self._get_data_dir(), "backups", "phase6")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target_path = os.path.join(backup_dir, f"gba-{timestamp}.sqlite3")
        with _DB_LOCK:
            src = sqlite3.connect(source_path, timeout=60)
            dst = sqlite3.connect(target_path, timeout=60)
            try:
                src.backup(dst)
                dst.commit()
                check = dst.execute("PRAGMA integrity_check").fetchone()
                if not check or str(check[0]).lower() != "ok":
                    raise RuntimeError(f"백업 무결성 검사 실패: {check}")
            finally:
                dst.close()
                src.close()
        return {
            "success": True,
            "path": target_path,
            "filename": os.path.basename(target_path),
            "size_bytes": os.path.getsize(target_path),
        }

    def _migration_journal_summary(self):
        rows = self._db_query(
            "SELECT status, COUNT(*) AS cnt FROM library_migrations WHERE migration_name = 'phase6' GROUP BY status"
        )
        counts = {str(row.get("status") or "unknown"): int(row.get("cnt") or 0) for row in rows}
        return {"total": sum(counts.values()), "counts": counts}

    def _upsert_migration_journal(self, future_id, legacy_id, source_path, target_path, status="pending", stage="planned", transport="", error=""):
        self._db_execute(
            """INSERT INTO library_migrations
               (migration_name, future_id, legacy_id, source_path, target_path, status, stage, transport, source_size, attempts, last_error, started_at, updated_at)
               VALUES ('phase6', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, '', CURRENT_TIMESTAMP)
               ON CONFLICT(migration_name, future_id) DO UPDATE SET
                 legacy_id=excluded.legacy_id, source_path=excluded.source_path, target_path=excluded.target_path,
                 status=excluded.status, stage=excluded.stage, transport=excluded.transport,
                 source_size=excluded.source_size, last_error=excluded.last_error, updated_at=CURRENT_TIMESTAMP""",
            (
                int(future_id), str(legacy_id or ""), str(source_path or ""), str(target_path or ""),
                str(status or "pending"), str(stage or "planned"), str(transport or ""),
                int(os.path.getsize(source_path)) if source_path and os.path.isfile(source_path) else 0,
                str(error or ""),
            ),
        )

    def _phase6_existing_targets(self):
        """library 하위 future_id 대상 디렉터리를 한 번만 스캔한다 (rclone 반복 listing 방지)."""
        root = os.path.join(self._get_emulatorjs_root(), "library")
        targets = {}
        if not os.path.isdir(root):
            return targets
        try:
            for platform_name in os.listdir(root):
                roms_dir = os.path.join(root, platform_name, "roms")
                if not os.path.isdir(roms_dir):
                    continue
                try:
                    names = os.listdir(roms_dir)
                except OSError:
                    continue
                for name in names:
                    try:
                        future_id = int(str(name))
                    except (TypeError, ValueError):
                        continue
                    candidate = os.path.join(roms_dir, str(name))
                    if os.path.isdir(candidate):
                        targets.setdefault(future_id, []).append(candidate)
        except OSError:
            pass
        return targets

    def _phase6_target_candidates(self, future_id):
        return list(self._phase6_existing_targets().get(int(future_id), []))

    def _phase6_preflight(self, repair=False):
        """Phase 6 전에 데이터/저장소 불변식을 검사하고 안전한 DB-only 복구만 선택적으로 수행한다."""
        repairs = []
        if repair:
            with _DB_LOCK:
                conn = self._get_db_conn(timeout=60)
                try:
                    self._backfill_future_game_ids(conn)
                    repaired_identity = self._backfill_content_identity(conn)
                    orphan_count = conn.execute(
                        "SELECT COUNT(*) FROM user_game_data u WHERE NOT EXISTS(SELECT 1 FROM games g WHERE g.id=u.game_id)"
                    ).fetchone()[0]
                    if orphan_count:
                        conn.execute(
                            "DELETE FROM user_game_data WHERE NOT EXISTS(SELECT 1 FROM games g WHERE g.id=user_game_data.game_id)"
                        )
                        repairs.append(f"고아 사용자 기록 {int(orphan_count)}개 정리")
                    if repaired_identity:
                        repairs.append(f"content identity {int(repaired_identity)}개 보정")
                    conn.commit()
                finally:
                    conn.close()

            # 존재 위치를 안전하게 찾을 수 있는 stale 경로만 DB를 복구한다. 파일 이동은 하지 않는다.
            stale_rows = self._db_query(
                "SELECT id, filename, file_path, core, platform FROM games WHERE COALESCE(deletion_status,'active')='active'"
            )
            recovered_count = 0
            for game in stale_rows:
                current = str(game.get("file_path") or "")
                if current and os.path.isfile(current):
                    continue
                recovered = self._resolve_existing_rom_path(
                    game.get("id"), filename=game.get("filename"), current_path=current,
                    core=game.get("core"), platform=game.get("platform"), update_db=True,
                )
                if recovered and os.path.isfile(recovered):
                    recovered_count += 1
            if recovered_count:
                repairs.append(f"ROM 경로 {recovered_count}개 복구")

        games = self._db_query(
            """SELECT id, future_id, filename, file_path, core, platform, layout_version,
                      COALESCE(deletion_status,'active') AS deletion_status,
                      COALESCE(health_status,'unverified') AS health_status,
                      COALESCE(content_identity_key,'') AS content_identity_key
               FROM games ORDER BY id"""
        )
        mappings = self._db_query("SELECT future_id, legacy_id, migration_status FROM game_id_map")
        map_by_legacy = {str(row.get("legacy_id") or ""): int(row.get("future_id") or 0) for row in mappings}
        future_counts = {}
        existing_targets = self._phase6_existing_targets()
        missing_future = []
        mapping_mismatch = []
        pending_delete = []
        missing_files = []
        unmanaged = []
        incomplete = []
        target_collisions = []
        identity_missing = []
        for game in games:
            gid = str(game.get("id") or "")
            fid = int(game.get("future_id") or 0)
            if fid <= 0:
                missing_future.append(gid)
            else:
                future_counts[fid] = future_counts.get(fid, 0) + 1
                if map_by_legacy.get(gid) != fid:
                    mapping_mismatch.append(gid)
                if int(game.get("layout_version") or 1) < 2:
                    found_targets = list(existing_targets.get(fid, []))
                    if found_targets:
                        target_collisions.append({"id": gid, "future_id": fid, "paths": found_targets})
            if str(game.get("deletion_status") or "active") != "active":
                pending_delete.append(gid)
            path = str(game.get("file_path") or "")
            if str(game.get("deletion_status") or "active") == "active":
                if not path or not os.path.isfile(path):
                    missing_files.append({"id": gid, "path": path})
                elif not self._is_managed_storage_path(path):
                    unmanaged.append({"id": gid, "path": path})
            if str(game.get("health_status") or "") == "incomplete":
                incomplete.append(gid)
            if not str(game.get("content_identity_key") or ""):
                identity_missing.append(gid)

        duplicate_future = [fid for fid, count in future_counts.items() if count > 1]
        orphan_rows = self._db_query(
            "SELECT user_id, game_id FROM user_game_data u WHERE NOT EXISTS(SELECT 1 FROM games g WHERE g.id=u.game_id)"
        )
        dependency_rows = [g.get("id") for g in games if str(g.get("health_status") or "") in ("chd_required", "bios_required")]
        root = self._get_emulatorjs_root()
        root_ready = bool(root and os.path.isdir(root) and os.access(root, os.W_OK))
        blockers = []
        warnings = []
        def add_blocker(code, label, count, sample=None):
            if count:
                blockers.append({"code": code, "label": label, "count": int(count), "sample": sample or []})
        add_blocker("future_id_missing", "future_id 누락", len(missing_future), missing_future[:10])
        add_blocker("future_id_duplicate", "future_id 중복", len(duplicate_future), duplicate_future[:10])
        add_blocker("mapping_mismatch", "game_id_map 불일치", len(mapping_mismatch), mapping_mismatch[:10])
        add_blocker("pending_delete", "삭제 대기/실패", len(pending_delete), pending_delete[:10])
        add_blocker("missing_file", "ROM 파일 없음", len(missing_files), missing_files[:10])
        add_blocker("unmanaged_path", "관리 저장소 밖 ROM", len(unmanaged), unmanaged[:10])
        add_blocker("incomplete_bundle", "불완전 디스크/CHD", len(incomplete), incomplete[:10])
        add_blocker("target_collision", "Phase 6 대상 경로 충돌", len(target_collisions), target_collisions[:10])
        add_blocker("content_identity_missing", "content identity 누락", len(identity_missing), identity_missing[:10])
        if not root_ready:
            blockers.append({"code": "root_not_writable", "label": "통합 라이브러리 루트 쓰기 불가", "count": 1, "sample": [root]})
        if orphan_rows:
            warnings.append({"code": "orphan_user_data", "label": "고아 사용자 기록", "count": len(orphan_rows), "sample": orphan_rows[:10]})
        if dependency_rows:
            warnings.append({"code": "runtime_dependency", "label": "실행 의존 파일(BIOS/CHD) 보완 필요", "count": len(dependency_rows), "sample": dependency_rows[:10]})

        return {
            "success": True,
            "ready": not blockers,
            "schema_version": self._get_db_schema_version(),
            "required_schema_version": self.DB_SCHEMA_VERSION,
            "total_games": len(games),
            "layout_v2": sum(1 for g in games if int(g.get("layout_version") or 1) >= 2),
            "blockers": blockers,
            "warnings": warnings,
            "repairs": repairs,
            "migration_journal": self._migration_journal_summary(),
            "emulatorjs_root": root,
        }

    def _select_launch_bios(self, game, game_file_path):
        """실행 시 사용할 BIOS를 DB 판정 우선으로 선택하고 실제 경로까지 확인합니다."""
        game_id = str(game.get("id") or "")
        needed_bios = str(game.get("needed_bios") or "").strip()
        if needed_bios:
            found = self._find_runtime_bios_path(needed_bios, game_file_path=game_file_path, game_id=game_id)
            return needed_bios, found

        filename = str(game.get("filename") or "").lower()
        raw_stem = os.path.splitext(os.path.basename(filename))[0].lower()
        core = str(game.get("core") or "").lower()
        platform = str(game.get("platform") or "").lower()
        is_arcade = core in ("arcade", "fbneo", "mame", "mame2003", "mame2003_plus") or platform in ("arcade", "neo-geo", "neogeo")

        candidates = []
        if platform in ("neo-geo", "neogeo") or (is_arcade and raw_stem.startswith(("mslug", "kof", "samsho", "fatfur", "garou"))):
            candidates = ["neogeo.zip"]
        elif is_arcade and raw_stem.startswith(("olds", "kov", "orlegend", "dmnfrnt")):
            candidates = ["pgm.zip"]
        elif is_arcade and raw_stem.startswith(("bldyror", "brvblade", "sfex", "rvschool", "starglad", "strider2", "techromn", "jgts", "raiden2", "raidendx")):
            candidates = ["acpsx.zip", "atluspsx.zip", "boardrom.zip"]
        elif core == "psx" or platform == "ps1":
            candidates = ["scph5501.bin", "scph1001.bin", "scph5500.bin", "scph5502.bin", "scph7001.bin"]
        elif platform == "fds" or filename.endswith(".fds"):
            candidates = ["disksys.rom"]
        elif core == "segacd" or platform == "segacd":
            candidates = ["bios_cd_u.bin", "bios_cd_j.bin", "bios_cd_e.bin"]
        elif core == "pce" or platform == "pce":
            candidates = ["syscard3.pce"]
        elif core in ("saturn", "segasaturn") or platform == "saturn":
            candidates = ["saturn_bios.bin", "mpr-17933.bin", "sega_101.bin"]
        elif core == "3do" or platform == "3do":
            candidates = ["3dobios.rom", "panafz10.bin", "panafz1.bin"]

        for candidate in candidates:
            found = self._find_runtime_bios_path(candidate, game_file_path=game_file_path, game_id=game_id)
            if found:
                return os.path.basename(found), found
        return None, None

    def _analysis_detail_cache_key(self, game, file_path, health_cache_key=None):
        """상세 분석과 health가 같은 캐시 키를 사용하도록 통일한다."""
        if health_cache_key:
            return str(health_cache_key)
        if not file_path or not os.path.isfile(file_path):
            return ""
        try:
            st = os.stat(file_path)
        except OSError:
            return ""
        payload = {
            "id": str(game.get("id") or ""),
            "core": str(game.get("core") or ""),
            "platform": str(game.get("platform") or ""),
            "game_code": str(game.get("game_code") or ""),
            "health_cache_key": str(health_cache_key if health_cache_key is not None else (game.get("health_cache_key") or "")),
            "path": os.path.realpath(file_path),
            "size": int(st.st_size),
            "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _effective_play_status(game):
        raw_status = str(game.get("play_status") or "untested").strip().lower()
        if raw_status not in ("untested", "booted", "verified", "issue"):
            raw_status = "untested"
        current_key = str(game.get("content_identity_key") or game.get("health_cache_key") or "")
        recorded_key = str(game.get("play_status_content_key") or game.get("play_status_health_key") or "")
        stale = raw_status != "untested" and current_key != recorded_key
        return {
            "status": "untested" if stale else raw_status,
            "raw_status": raw_status,
            "stale": bool(stale),
            "updated_at": str(game.get("play_status_updated_at") or ""),
            "user_id": int(game.get("play_status_user_id") or 0),
            "last_booted_at": str(game.get("last_booted_at") or ""),
        }

    def _record_game_boot(self, game_id, user_id):
        rows = self._db_query(
            "SELECT id, COALESCE(health_cache_key, '') AS health_cache_key, COALESCE(content_identity_key, '') AS content_identity_key, "
            "COALESCE(play_status, 'untested') AS play_status, "
            "COALESCE(play_status_health_key, '') AS play_status_health_key, COALESCE(play_status_content_key, '') AS play_status_content_key, "
            "COALESCE(play_status_updated_at, '') AS play_status_updated_at, "
            "COALESCE(play_status_user_id, 0) AS play_status_user_id, "
            "COALESCE(last_booted_at, '') AS last_booted_at "
            "FROM games WHERE id = ? AND COALESCE(deletion_status, 'active') = 'active'",
            (game_id,),
        )
        if not rows:
            return {"success": False, "error": "게임을 찾을 수 없습니다."}
        game = rows[0]
        current = self._effective_play_status(game)
        now_str = _get_kst_now_str()
        if current["status"] in ("verified", "issue"):
            self._db_execute("UPDATE games SET last_booted_at = ? WHERE id = ?", (now_str, game_id))
        else:
            self._db_execute(
                "UPDATE games SET play_status = 'booted', play_status_updated_at = ?, "
                "play_status_user_id = ?, play_status_content_key = CASE WHEN COALESCE(content_identity_key, '') != '' THEN content_identity_key ELSE COALESCE(health_cache_key, '') END, last_booted_at = ? WHERE id = ?",
                (now_str, int(user_id or 0), now_str, game_id),
            )
        refreshed = self._db_query(
            "SELECT COALESCE(health_cache_key, '') AS health_cache_key, COALESCE(content_identity_key, '') AS content_identity_key, COALESCE(play_status, 'untested') AS play_status, "
            "COALESCE(play_status_health_key, '') AS play_status_health_key, COALESCE(play_status_content_key, '') AS play_status_content_key, COALESCE(play_status_updated_at, '') AS play_status_updated_at, "
            "COALESCE(play_status_user_id, 0) AS play_status_user_id, COALESCE(last_booted_at, '') AS last_booted_at FROM games WHERE id = ?",
            (game_id,),
        )
        return {"success": True, "play": self._effective_play_status(refreshed[0] if refreshed else game)}

    def _set_game_play_status(self, game_id, user_id, status):
        status = str(status or "").strip().lower()
        if status not in ("untested", "verified", "issue"):
            return {"success": False, "error": "지원하지 않는 플레이 상태입니다."}
        rows = self._db_query(
            "SELECT id, COALESCE(health_cache_key, '') AS health_cache_key, COALESCE(content_identity_key, '') AS content_identity_key FROM games "
            "WHERE id = ? AND COALESCE(deletion_status, 'active') = 'active'",
            (game_id,),
        )
        if not rows:
            return {"success": False, "error": "게임을 찾을 수 없습니다."}
        now_str = _get_kst_now_str()
        content_key = str(rows[0].get("content_identity_key") or rows[0].get("health_cache_key") or "")
        self._db_execute(
            "UPDATE games SET play_status = ?, play_status_updated_at = ?, play_status_user_id = ?, play_status_content_key = ? WHERE id = ?",
            (status, now_str, int(user_id or 0), content_key if status != "untested" else "", game_id),
        )
        refreshed = self._db_query(
            "SELECT COALESCE(health_cache_key, '') AS health_cache_key, COALESCE(content_identity_key, '') AS content_identity_key, COALESCE(play_status, 'untested') AS play_status, "
            "COALESCE(play_status_health_key, '') AS play_status_health_key, COALESCE(play_status_content_key, '') AS play_status_content_key, COALESCE(play_status_updated_at, '') AS play_status_updated_at, "
            "COALESCE(play_status_user_id, 0) AS play_status_user_id, COALESCE(last_booted_at, '') AS last_booted_at FROM games WHERE id = ?",
            (game_id,),
        )
        return {"success": True, "play": self._effective_play_status(refreshed[0] if refreshed else rows[0])}

    def _build_analysis_detail(self, game_id, user_id, is_admin=False, force=False):
        rows = self._db_query(
            "SELECT id, filename, file_path, title, game_code, core, platform, size_bytes, mtime, needed_bios, "
            "COALESCE(health_status, 'unverified') AS health_status, COALESCE(missing_roms, '') AS missing_roms, "
            "COALESCE(rom_crc32, '') AS rom_crc32, COALESCE(rom_md5, '') AS rom_md5, COALESCE(rom_sha1, '') AS rom_sha1, "
            "COALESCE(serial_code, '') AS serial_code, COALESCE(normalized_title, '') AS normalized_title, "
            "COALESCE(source_system, '') AS source_system, COALESCE(metadata_source, '') AS metadata_source, "
            "COALESCE(metadata_confidence, 0) AS metadata_confidence, COALESCE(region_tag, '') AS region_tag, "
            "COALESCE(revision_tag, '') AS revision_tag, COALESCE(disc_number, 0) AS disc_number, "
            "COALESCE(content_flags, '') AS content_flags, COALESCE(health_cache_key, '') AS health_cache_key, COALESCE(content_identity_key, '') AS content_identity_key, "
            "COALESCE(analysis_json, '') AS analysis_json, COALESCE(analysis_cache_key, '') AS analysis_cache_key, "
            "COALESCE(play_status, 'untested') AS play_status, COALESCE(play_status_updated_at, '') AS play_status_updated_at, "
            "COALESCE(play_status_user_id, 0) AS play_status_user_id, COALESCE(play_status_health_key, '') AS play_status_health_key, COALESCE(play_status_content_key, '') AS play_status_content_key, "
            "COALESCE(last_booted_at, '') AS last_booted_at, COALESCE(deletion_status, 'active') AS deletion_status "
            "FROM games WHERE id = ?",
            (game_id,),
        )
        if not rows:
            return {"success": False, "error": "게임을 찾을 수 없습니다."}
        game = rows[0]
        if str(game.get("deletion_status") or "active") != "active":
            return {"success": False, "error": "삭제 대기 중인 게임입니다."}

        evaluation = self._evaluate_rom_analysis(game, force=bool(force))
        target = evaluation.get("target")
        if target:
            self._persist_rom_analysis(game_id, target)
            game.update({
                "health_status": target["status"],
                "missing_roms": target["reason"],
                "metadata_source": target["metadata_source"],
                "metadata_confidence": target["metadata_confidence"],
                "source_system": target["source_system"],
                "needed_bios": target["needed_bios"],
                "health_cache_key": target["cache_key"],
                "analysis_json": target["analysis_json"],
                "analysis_cache_key": target["analysis_cache_key"],
                "content_identity_key": target["content_identity_key"],
            })
        file_path = str(evaluation.get("file_path") or "")
        analysis = evaluation.get("snapshot") or self._cached_analysis_snapshot(game) or {}
        cache_reused = bool(evaluation.get("cache_reused"))
        analysis_error = str(evaluation.get("error") or "")
        analysis_stale = bool(analysis_error and evaluation.get("kind") == "failed")

        bios_name = str((analysis or {}).get("needed_bios") or game.get("needed_bios") or "").strip()
        bios_path = self._find_runtime_bios_path(bios_name, game_file_path=file_path, game_id=game_id) if bios_name else None
        play = self._effective_play_status(game)
        return {
            "success": True,
            "game_id": str(game_id),
            "title": game.get("title") or game.get("filename") or str(game_id),
            "file": {
                "name": game.get("filename") or "",
                "size_bytes": int(game.get("size_bytes") or 0),
                "mtime": float(game.get("mtime") or 0),
                "exists": bool(file_path),
                "relative_path": game.get("filename") or "",
                "server_path": file_path if is_admin else "",
            },
            "identity": {
                "core": game.get("core") or "",
                "platform": game.get("platform") or "",
                "game_code": game.get("game_code") or "",
                "serial_code": game.get("serial_code") or "",
                "normalized_title": game.get("normalized_title") or "",
                "region_tag": game.get("region_tag") or "",
                "revision_tag": game.get("revision_tag") or "",
                "disc_number": int(game.get("disc_number") or 0),
                "content_flags": game.get("content_flags") or "",
                "source_system": game.get("source_system") or "",
                "metadata_source": game.get("metadata_source") or "",
                "metadata_confidence": int(game.get("metadata_confidence") or 0),
            },
            "hashes": {
                "crc32": game.get("rom_crc32") or "",
                "md5": game.get("rom_md5") or "",
                "sha1": game.get("rom_sha1") or "",
            },
            "health": {
                "status": analysis.get("health_status") or game.get("health_status") or "unverified",
                "reason": analysis.get("health_reason") or game.get("missing_roms") or "",
                "updated_at": analysis.get("analysis_updated_at") or "",
            },
            "bios": {
                "name": bios_name,
                "available": bool(bios_path and os.path.isfile(bios_path)),
            },
            "play": play,
            "analysis": analysis or {},
            "analysis_cache_reused": cache_reused,
            "analysis_stale": analysis_stale,
            "analysis_error": analysis_error,
        }

    def _build_launch_plan(self, game_id, user_id):
        """게임 실행 직전 ROM/BIOS/세이브/전달방식을 한 번에 계산합니다."""
        rows = self._db_query(
            """SELECT id, filename, file_path, title, game_code, core, platform, needed_bios,
                      COALESCE(health_status, 'pass') AS health_status,
                      COALESCE(missing_roms, '') AS missing_roms,
                      COALESCE(deletion_status, 'active') AS deletion_status
               FROM games WHERE id = ?""",
            (game_id,),
        )
        if not rows:
            return {"success": False, "launchable": False, "error": "게임을 찾을 수 없습니다."}

        game = rows[0]
        if str(game.get("deletion_status") or "active") != "active":
            return {"success": True, "launchable": False, "blocked_reason": "삭제 대기 중인 게임입니다."}

        root_file_path = str(game.get("file_path") or "")
        if not os.path.isfile(root_file_path):
            recovered = self._resolve_existing_rom_path(
                game_id,
                filename=game.get("filename"),
                current_path=root_file_path,
                core=game.get("core"),
                platform=game.get("platform"),
                update_db=False,
            )
            if recovered and os.path.isfile(recovered):
                root_file_path = recovered
            else:
                return {
                    "success": True,
                    "launchable": False,
                    "blocked_reason": "등록된 ROM 파일을 찾을 수 없습니다.",
                    "health_status": "missing_file",
                }

        ext = os.path.splitext(root_file_path)[1].lower()
        source_size = 0
        try:
            source_size = int(os.path.getsize(root_file_path))
        except OSError:
            source_size = 0

        delivery_mode = "direct"
        browser_unpack = False
        output_filename = os.path.basename(game.get("filename") or root_file_path)
        bundle_files = []

        if ext == ".7z":
            delivery_mode = "convert_7z"
            browser_unpack = True
            output_filename = os.path.splitext(output_filename)[0] + ".zip"
        elif ext == ".zip":
            delivery_mode = "zip"
            browser_unpack = True
        elif ext in (".cue", ".gdi", ".bin", ".iso", ".img"):
            bundle_files = [candidate for candidate in _collect_disk_bundle_paths(root_file_path) if os.path.isfile(candidate)]
            if len(bundle_files) > 1:
                delivery_mode = "bundle_zip"
                browser_unpack = True
                output_filename = os.path.splitext(output_filename)[0] + ".zip"
                try:
                    source_size = sum(int(os.path.getsize(candidate)) for candidate in bundle_files)
                except OSError:
                    pass

        rom_url = f"{ROUTE_BASE}/rom/{game_id}/{urllib.parse.quote(output_filename)}"

        bios_name, bios_path = self._select_launch_bios(game, root_file_path)
        bios_available = bool(bios_path and os.path.isfile(bios_path))
        bios_size = 0
        if bios_available:
            try:
                bios_size = int(os.path.getsize(bios_path))
            except OSError:
                bios_size = 0
        bios_url = (
            f"{ROUTE_BASE}/bios/{urllib.parse.quote(bios_name)}?game_id={urllib.parse.quote(str(game_id))}"
            if bios_name and bios_available
            else None
        )

        has_save = False
        has_state = False
        try:
            user_saves_dir = self._get_user_saves_dir(user_id)
            if os.path.isdir(user_saves_dir):
                save_candidates = [
                    os.path.join(user_saves_dir, f"{game_id}.sav"),
                    os.path.join(user_saves_dir, f"{game_id}.state"),
                    os.path.join(user_saves_dir, f"{game_id}_slot1.state"),
                ]
                has_save = any(os.path.isfile(path) and os.path.getsize(path) > 0 for path in save_candidates)
                has_state = any(os.path.isfile(path) and os.path.getsize(path) > 0 for path in save_candidates[1:])
        except OSError:
            has_save = False
            has_state = False

        return {
            "success": True,
            "launchable": True,
            "game_id": str(game_id),
            "core": game.get("core") or "",
            "platform": game.get("platform") or "",
            "health_status": game.get("health_status") or "pass",
            "missing_roms": game.get("missing_roms") or "",
            "delivery_mode": delivery_mode,
            "browser_unpack": bool(browser_unpack),
            "rom_url": rom_url,
            "rom_source_size": source_size,
            "bundle_file_count": len(bundle_files),
            "bios_name": bios_name or "",
            "bios_required": bool(str(game.get("needed_bios") or "").strip() or game.get("health_status") == "bios_required"),
            "bios_available": bios_available,
            "bios_url": bios_url,
            "bios_size": bios_size,
            "has_save": 1 if has_save else 0,
            "has_state": 1 if has_state else 0,
            "state_url": f"{ROUTE_BASE}/state/{game_id}" if has_state else None,
        }

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

        rows = self._db_query("SELECT file_path, filename, core, platform FROM games WHERE id = ?", (game_id,))
        if not rows:
            abort(404, "ROM file not found")
        row = rows[0]
        root_file_path = row["file_path"]
        if not os.path.isfile(root_file_path):
            recovered_path = self._resolve_existing_rom_path(
                game_id,
                filename=row["filename"],
                current_path=root_file_path,
                core=row["core"],
                platform=row["platform"],
                update_db=True,
            )
            if not recovered_path:
                abort(404, "ROM file not found")
            root_file_path = recovered_path

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
        """small/large WebP를 우선하고 기존 원본 커버로 안전하게 fallback한다."""
        from flask import Response, abort, request

        if _get_current_user_id() <= 0:
            abort(401, "Authentication required")

        rows = self._db_query(
            """SELECT cover_path, COALESCE(cover_large_path,'') AS cover_large_path,
                      COALESCE(cover_thumbnail_path,'') AS cover_thumbnail_path
               FROM games WHERE id = ?""",
            (game_id,),
        )
        if not rows:
            abort(404, "Cover image not found")
        game = rows[0]
        size = str(request.args.get("size", "large") or "large").strip().lower()
        if size in ("small", "thumb", "thumbnail"):
            candidates = (game.get("cover_thumbnail_path"), game.get("cover_large_path"), game.get("cover_path"))
        else:
            candidates = (game.get("cover_large_path"), game.get("cover_path"), game.get("cover_thumbnail_path"))
        cover_path = next((str(path).strip() for path in candidates if path and os.path.isfile(str(path))), "")
        if not cover_path:
            abort(404, "Cover image not found")

        ext = os.path.splitext(cover_path)[1].lower().replace(".", "")
        mime = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/webp"
        try:
            with open(cover_path, "rb") as f:
                data = f.read()
        except OSError:
            abort(404, "Cover image not found")
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

        allowed_rom_exts = set(SUPPORTED_SYSTEMS.keys()) | {".zip", ".7z", ".bin", ".rom", ".cue", ".gdi", ".iso", ".img", ".chd", ".m3u"}
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

            # 업로드 핸들러는 파일 수신까지만 담당한다. 실제 기종 폴더 배치,
            # 7z→ZIP 변환, 디스크 번들 이동, DB 등록은 Library Sync Engine에서 일관되게 처리한다.
            dest_path = os.path.join(dest_dir, safe_filename)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(dest_dir, f"{base_n}_{counter}{ext_n}")
                counter += 1
            shutil.move(temp_dest, dest_path)
            safe_filename = os.path.basename(dest_path)

            defer_sync = str(request.form.get("defer_sync", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
            if not defer_sync:
                # 구버전 프런트 호환: 단일 업로드 요청은 즉시 공통 ingest 파이프라인으로 반영한다.
                self._run_library_sync("ingest")

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
            self._finalize_cover_source(game_id, dest_path, force=True)
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
        elif ext in (".iso", ".chd", ".cue", ".pbp", ".gdi", ".m3u"):
            if ext == ".gdi":
                detected_platform = "Dreamcast"
            elif ext == ".m3u":
                detected_platform = "멀티디스크 플레이리스트 (스캔 시 실제 디스크로 기종 판별)"
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
            self._run_library_sync("ingest")
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
        self._run_library_sync("ingest")
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
                self._finalize_cover_source(game_id, cover_path, force=True)
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
            "library_sync", "scan_new_roms", "scan_roms", "full_scan", "health_check", "health_refresh", "health_progress",
            "fetch_missing_covers", "delete_game", "cancel_delete_game", "delete_queue_status", "update_title", "save_settings", "search_artwork", "set_artwork",
            "phase6_preflight", "phase6_repair", "phase6_backup", "migration_journal_status", "cover_webp_refresh", "cover_webp_progress",
        }
        if action in admin_actions and not is_admin:
            return {"success": False, "error": "관리자(admin) 권한이 필요합니다."}

        try:
            if action == "list_games":
                # 목록 화면은 DB-only 서버 페이징을 사용한다. 최초 진입이 rclone/GDrive,
                # 커버/BIOS/세이브 디렉터리 상태에 영향을 받지 않도록 이 경로에서는
                # 파일시스템 조회/복구/자동 스캔을 수행하지 않는다.
                game_count_row = self._db_query(
                    "SELECT COUNT(*) AS cnt FROM games WHERE COALESCE(deletion_status, 'active') = 'active'"
                )
                library_total_count = int(game_count_row[0]["cnt"] if game_count_row else 0)
                pending_delete_row = self._db_query(
                    "SELECT COUNT(*) AS cnt FROM games WHERE COALESCE(deletion_status, 'active') != 'active'"
                )
                pending_delete_count = int(pending_delete_row[0]["cnt"] if pending_delete_row else 0)

                def _request_int(name, default, minimum, maximum):
                    try:
                        value = int(request.args.get(name, str(default)))
                    except (TypeError, ValueError):
                        value = default
                    return max(minimum, min(maximum, value))

                page_limit = _request_int("limit", 40, 1, 100)
                page_offset = _request_int("offset", 0, 0, 100000000)
                category = str(request.args.get("category", "all") or "all").strip().lower()
                sort_mode = str(request.args.get("sort", "newest") or "newest").strip().lower()
                status_filter = str(request.args.get("status", "all") or "all").strip().lower()
                favorite_only = str(request.args.get("favorite_only", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
                search_query = str(request.args.get("q", "") or "").strip().casefold()

                where_parts = ["COALESCE(g.deletion_status, 'active') = 'active'"]
                where_args = []

                if favorite_only:
                    where_parts.append("COALESCE(u.is_favorite, 0) = 1")

                allowed_statuses = {
                    "pass", "bios_required", "chd_required", "incomplete",
                    "unsupported", "unverified", "reclassify_required",
                }
                if status_filter in allowed_statuses:
                    if status_filter == "unverified":
                        where_parts.append(
                            "COALESCE(g.health_status, 'unverified') IN ('unverified', 'parent_required', 'bad_dump_or_unknown')"
                        )
                    else:
                        where_parts.append("COALESCE(g.health_status, 'unverified') = ?")
                        where_args.append(status_filter)

                core_expr = "LOWER(COALESCE(g.core, ''))"
                platform_expr = "LOWER(COALESCE(g.platform, ''))"
                category_sql = {
                    "snes": f"({core_expr} = 'snes' OR {platform_expr} = 'snes')",
                    "gba": f"({core_expr} = 'gba' OR {platform_expr} = 'gba')",
                    "nes": f"({core_expr} = 'nes' OR {platform_expr} IN ('nes', 'fds'))",
                    "gb": f"({core_expr} IN ('gb', 'gbc') OR {platform_expr} IN ('gb', 'gbc'))",
                    "nds": f"({core_expr} = 'nds' OR {platform_expr} = 'nds')",
                    "n64": f"({core_expr} = 'n64' OR {platform_expr} = 'n64')",
                    "genesis": f"({core_expr} IN ('segamd', 'segams', 'segagg', 'sega32x', 'segacd', 'segasaturn') OR {platform_expr} IN ('genesis', 'mastersystem', 'gamegear', 'sega32x', 'saturn'))",
                    "psx": f"({core_expr} = 'psx' OR {platform_expr} = 'ps1')",
                    "psp": f"({core_expr} = 'psp' OR {platform_expr} = 'psp')",
                    "arcade": f"({core_expr} IN ('arcade', 'mame2003') OR {platform_expr} IN ('arcade', 'neo-geo'))",
                    "neogeo": f"({platform_expr} IN ('neo-geo', 'neogeo'))",
                    "other": f"({platform_expr} NOT IN ('snes', 'gba', 'nes', 'fds', 'gb', 'gbc', 'nds', 'n64', 'genesis', 'mastersystem', 'gamegear', 'sega32x', 'saturn', 'ps1', 'psp', 'arcade', 'neo-geo', 'neogeo'))",
                }
                if category in category_sql:
                    where_parts.append(category_sql[category])

                if search_query:
                    escaped = search_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    pattern = f"%{escaped}%"
                    where_parts.append(
                        "(LOWER(COALESCE(g.title, '')) LIKE ? ESCAPE '\\' "
                        "OR LOWER(COALESCE(g.filename, '')) LIKE ? ESCAPE '\\' "
                        "OR LOWER(COALESCE(g.game_code, '')) LIKE ? ESCAPE '\\')"
                    )
                    where_args.extend((pattern, pattern, pattern))

                where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
                join_sql = " LEFT JOIN user_game_data u ON g.id = u.game_id AND u.user_id = ?"
                base_args = [user_id] + where_args

                filtered_count_row = self._db_query(
                    "SELECT COUNT(*) AS cnt FROM games g" + join_sql + where_sql,
                    tuple(base_args),
                )
                filtered_total_count = int(filtered_count_row[0]["cnt"] if filtered_count_row else 0)

                if sort_mode == "title":
                    order_sql = " ORDER BY LOWER(COALESCE(g.title, '')) ASC, g.id ASC"
                elif sort_mode == "recent":
                    order_sql = (
                        " ORDER BY CASE WHEN COALESCE(u.last_played_at, '') = '' THEN 1 ELSE 0 END ASC, "
                        "u.last_played_at DESC, g.added_at DESC, g.id ASC"
                    )
                else:
                    sort_mode = "newest"
                    order_sql = " ORDER BY g.added_at DESC, g.id ASC"

                games = self._db_query(
                    """SELECT g.id, g.filename, g.title, g.game_code,
                              g.size_bytes, g.cover_path, COALESCE(g.cover_large_path,'') AS cover_large_path,
                              COALESCE(g.cover_thumbnail_path,'') AS cover_thumbnail_path, COALESCE(g.cover_revision,0) AS cover_revision,
                              g.core, g.platform, g.needed_bios,
                              COALESCE(g.health_status, 'pass') AS health_status,
                              COALESCE(g.missing_roms, '') AS missing_roms,
                              COALESCE(g.metadata_source, '') AS metadata_source,
                              COALESCE(g.metadata_confidence, 0) AS metadata_confidence,
                              COALESCE(g.region_tag, '') AS region_tag,
                              COALESCE(g.revision_tag, '') AS revision_tag,
                              COALESCE(g.disc_number, 0) AS disc_number,
                              COALESCE(g.content_flags, '') AS content_flags,
                              COALESCE(g.health_cache_key, '') AS health_cache_key,
                              COALESCE(g.content_identity_key, '') AS content_identity_key,
                              COALESCE(g.play_status, 'untested') AS play_status,
                              COALESCE(g.play_status_health_key, '') AS play_status_health_key,
                              COALESCE(g.play_status_content_key, '') AS play_status_content_key,
                              COALESCE(g.play_status_updated_at, '') AS play_status_updated_at,
                              COALESCE(g.play_status_user_id, 0) AS play_status_user_id,
                              COALESCE(g.last_booted_at, '') AS last_booted_at,
                              COALESCE(u.is_favorite, 0) AS is_favorite,
                              u.last_played_at
                       FROM games g"""
                    + join_sql
                    + where_sql
                    + order_sql
                    + " LIMIT ? OFFSET ?",
                    tuple(base_args + [page_limit, page_offset]),
                )

                visible_games = []
                for g in games:
                    if g.get("health_status") in ("parent_required", "bad_dump_or_unknown"):
                        g["health_status"] = "unverified"
                        if not g.get("missing_roms"):
                            g["missing_roms"] = "최신 rom-analyzer 재진단이 필요한 기존 상태입니다."
                    gid = g["id"]
                    play = self._effective_play_status(g)
                    g["play_status"] = play["status"]
                    g["play_status_stale"] = 1 if play["stale"] else 0
                    g["play_status_updated_at"] = play["updated_at"]
                    g["last_booted_at"] = play["last_booted_at"]
                    g.pop("health_cache_key", None)
                    g.pop("content_identity_key", None)
                    g.pop("play_status_health_key", None)
                    g.pop("play_status_content_key", None)
                    g.pop("play_status_user_id", None)
                    # 목록 응답은 DB에 저장된 값만 사용한다. 실제 파일 상태는 목록 렌더 후
                    # runtime_state 액션이 비동기로 보정한다.
                    g["relative_path"] = g.get("filename") or ""
                    g["has_save"] = 0
                    g["has_state"] = 0
                    g["runtime_state_loaded"] = False

                    url_fname = g["filename"]
                    if url_fname.lower().endswith(".7z"):
                        url_fname = os.path.splitext(url_fname)[0] + ".zip"
                    elif os.path.splitext(url_fname)[1].lower() in (".cue", ".gdi"):
                        url_fname = os.path.splitext(url_fname)[0] + ".zip"
                    g["rom_url"] = f"{ROUTE_BASE}/rom/{gid}/{urllib.parse.quote(url_fname)}"
                    g["save_url"] = f"{ROUTE_BASE}/save/{gid}"
                    g["state_url"] = f"{ROUTE_BASE}/state/{gid}"
                    revision = int(g.get("cover_revision") or 0)
                    g["cover_url"] = f"{ROUTE_BASE}/cover/{gid}?size=small&rev={revision}"
                    g["has_needed_bios"] = 1
                    # 목록 화면은 커버의 실제 서버 경로가 필요하지 않다.
                    g["cover_path"] = bool(g.get("cover_path") or g.get("cover_large_path") or g.get("cover_thumbnail_path"))
                    g.pop("cover_large_path", None)
                    g.pop("cover_thumbnail_path", None)
                    g.pop("file_path", None)
                    visible_games.append(g)

                response = {
                    "success": True,
                    "games": visible_games,
                    "total_count": filtered_total_count,
                    "library_total_count": library_total_count,
                    "pending_delete_count": pending_delete_count,
                    "offset": page_offset,
                    "limit": page_limit,
                    "next_offset": page_offset + len(visible_games),
                    "has_more": page_offset + len(visible_games) < filtered_total_count,
                    "user_id": user_id,
                    "is_admin": is_admin,
                }

                return response

            elif action == "launch_plan":
                game_id = str(request.args.get("game_id", "") or "").strip()
                if not game_id:
                    return {"success": False, "launchable": False, "error": "game_id 파라미터가 필요합니다."}
                return self._build_launch_plan(game_id, user_id)

            elif action == "analysis_detail":
                game_id = str(request.args.get("game_id", "") or "").strip()
                if not game_id:
                    return {"success": False, "error": "game_id 파라미터가 필요합니다."}
                force = str(request.args.get("refresh", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
                return self._build_analysis_detail(game_id, user_id, is_admin=is_admin, force=force)

            elif action == "runtime_state":
                # 최초 목록 렌더와 분리된 실제 파일 상태 조회. rclone/GDrive 메타데이터
                # 접근이 느려도 게임 카드 자체는 이미 DB 응답으로 표시된 뒤다.
                raw_ids = str(request.args.get("game_ids", "") or "")
                game_ids = []
                seen_game_ids = set()
                for raw_id in raw_ids.split(","):
                    game_id = raw_id.strip()
                    if not game_id or game_id in seen_game_ids:
                        continue
                    seen_game_ids.add(game_id)
                    game_ids.append(game_id)
                    if len(game_ids) >= 100:
                        break

                existing_saves = set()
                if game_ids:
                    try:
                        user_saves_dir = self._get_user_saves_dir(user_id)
                        if os.path.isdir(user_saves_dir):
                            for entry in os.scandir(user_saves_dir):
                                if entry.name.startswith(".") or not entry.is_file():
                                    continue
                                try:
                                    if entry.stat().st_size > 0:
                                        existing_saves.add(entry.name)
                                except OSError:
                                    continue
                    except OSError as e:
                        logger.warning(f"[{SELF_ID}] Save runtime state list error: {e}")

                game_states = {}
                for game_id in game_ids:
                    has_sav = f"{game_id}.sav" in existing_saves
                    has_state = (
                        f"{game_id}.state" in existing_saves
                        or f"{game_id}_slot1.state" in existing_saves
                    )
                    game_states[game_id] = {
                        "has_save": 1 if (has_sav or has_state) else 0,
                        "has_state": 1 if has_state else 0,
                    }

                result = {"success": True, "game_states": game_states}
                include_globals = str(request.args.get("include_globals", "0") or "0").strip().lower() in (
                    "1", "true", "yes", "on"
                )
                if include_globals:
                    result["available_bios"] = self._list_available_bios_names()
                    result["config"] = {
                        "cloud_save_enabled": str(self._get_setting("CLOUD_SAVE_ENABLED", "1")).lower() in ("1", "true", "yes", "on"),
                        "auto_save_interval_sec": int(self._get_setting("AUTO_SAVE_INTERVAL_SEC", "60")),
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
                    }
                return result

            elif action == "cover_webp_refresh":
                force = str(request.args.get("force", "0") or "0").lower() in ("1", "true", "yes", "on")
                return self._start_cover_variant_rebuild(force=force)

            elif action == "cover_webp_progress":
                with _COVER_VARIANT_PROGRESS_LOCK:
                    return {"success": True, "progress": dict(_COVER_VARIANT_PROGRESS)}

            elif action == "phase6_preflight":
                return self._phase6_preflight(repair=False)

            elif action == "phase6_repair":
                return self._phase6_preflight(repair=True)

            elif action == "phase6_backup":
                return self._create_phase6_db_backup()

            elif action == "migration_journal_status":
                return {"success": True, "journal": self._migration_journal_summary()}

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

            elif action == "library_sync":
                mode = str(request.args.get("mode", "sync") or "sync").strip().lower()
                if mode not in {"ingest", "sync", "rebuild", "diagnose"}:
                    return {"success": False, "error": f"지원하지 않는 라이브러리 동기화 모드입니다: {mode}"}

                with _SCAN_PROGRESS_LOCK:
                    scan_busy = bool(_SCAN_PROGRESS.get("is_running")) or _SCAN_PROGRESS.get("status") == "queued"
                with _HEALTH_PROGRESS_LOCK:
                    health_busy = bool(_HEALTH_PROGRESS.get("is_running")) or _HEALTH_PROGRESS.get("status") == "queued"

                if mode == "diagnose":
                    if scan_busy:
                        return {"success": False, "error": "라이브러리 동기화가 진행 중입니다. 완료 후 전체 ROM 분석을 실행하세요."}
                    launch = False
                    with _HEALTH_PROGRESS_LOCK:
                        running = bool(_HEALTH_PROGRESS.get("is_running"))
                        queued = _HEALTH_PROGRESS.get("status") == "queued"
                        if not running and not queued:
                            _HEALTH_PROGRESS.update({
                                "is_running": False, "current": 0, "total": 0, "current_file": "",
                                "status": "queued", "changed": 0, "cached": 0, "failed": 0, "updated_at": time.time(),
                            })
                            launch = True
                        progress = dict(_HEALTH_PROGRESS)
                    if launch:
                        threading.Thread(target=self._run_library_sync_background, args=("diagnose",), daemon=True).start()
                    return {"success": True, "mode": mode, "progress": progress}

                if health_busy:
                    return {"success": False, "error": "전체 ROM 분석이 진행 중입니다. 완료 후 라이브러리 작업을 실행하세요."}

                if mode == "rebuild":
                    if not scan_busy:
                        threading.Thread(target=self._run_library_sync_background, args=("rebuild",), daemon=True).start()
                    return {"success": True, "mode": mode, "message": "라이브러리 전체 재구축이 시작되었습니다."}

                if scan_busy:
                    return {"success": False, "error": "다른 라이브러리 동기화 작업이 진행 중입니다."}
                stats = self._run_library_sync(mode)
                if isinstance(stats, dict) and stats.get("busy"):
                    return stats
                return {
                    "success": True,
                    "mode": mode,
                    "message": "라이브러리 동기화가 완료되었습니다.",
                    "stats": stats,
                }

            elif action == "scan_new_roms":
                res = self._run_library_sync("ingest")
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
                cover_index = self._build_cover_file_index()
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
                        cover_index=cover_index,
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
                with _HEALTH_PROGRESS_LOCK:
                    health_busy = bool(_HEALTH_PROGRESS.get("is_running")) or _HEALTH_PROGRESS.get("status") == "queued"
                if health_busy:
                    return {"success": False, "error": "전체 ROM 분석이 진행 중입니다. 완료 후 라이브러리 전체 재구축을 실행하세요."}
                with _SCAN_PROGRESS_LOCK:
                    is_running = _SCAN_PROGRESS.get("is_running", False)
                if not is_running:
                    threading.Thread(target=self._run_library_sync_background, args=("rebuild",), daemon=True).start()
                return {"success": True, "message": "모든 ROM 파일의 전체 재스캔이 시작되었습니다."}

            elif action == "health_refresh":
                with _SCAN_PROGRESS_LOCK:
                    scan_running = bool(_SCAN_PROGRESS.get("is_running"))
                if scan_running:
                    return {"success": False, "error": "ROM 스캔이 진행 중입니다. 완료 후 전체 ROM 분석을 실행하세요."}
                launch = False
                with _HEALTH_PROGRESS_LOCK:
                    running = bool(_HEALTH_PROGRESS.get("is_running"))
                    queued = _HEALTH_PROGRESS.get("status") == "queued"
                    if not running and not queued:
                        _HEALTH_PROGRESS.update({
                            "is_running": False, "current": 0, "total": 0, "current_file": "",
                            "status": "queued", "changed": 0, "cached": 0, "failed": 0, "updated_at": time.time(),
                        })
                        launch = True
                    progress = dict(_HEALTH_PROGRESS)
                if launch:
                    threading.Thread(target=self._run_library_sync_background, args=("diagnose",), daemon=True).start()
                return {"success": True, "progress": progress}

            elif action == "health_progress":
                with _HEALTH_PROGRESS_LOCK:
                    progress = dict(_HEALTH_PROGRESS)
                return {"success": True, "progress": progress}

            elif action == "health_check":
                all_games = self._db_query(
                    "SELECT id, filename, file_path, core, platform, title, game_code, needed_bios, "
                    "metadata_source, metadata_confidence, source_system, "
                    "COALESCE(health_status, 'unverified') AS health_status, "
                    "COALESCE(missing_roms, '') AS missing_roms FROM games ORDER BY title ASC"
                )

                buckets = {
                    "pass": [], "bios_required": [], "chd_required": [], "incomplete": [],
                    "missing_file": [], "path_mismatch": [],
                    "unsupported": [], "unverified": [], "reclassify_required": [],
                }
                for game in all_games:
                    status = str(game.get("health_status") or "unverified").strip()
                    if status in ("parent_required", "bad_dump_or_unknown"):
                        status = "unverified"
                    if status not in buckets:
                        status = "unverified"
                    item = {
                        "id": game.get("id"),
                        "title": game.get("title") or game.get("filename") or "",
                        "filename": game.get("filename") or "",
                        "core": game.get("core") or "",
                        "platform": game.get("platform") or "",
                        "health_status": status,
                        "metadata_source": game.get("metadata_source") or "",
                        "metadata_confidence": game.get("metadata_confidence") or 0,
                        "source_system": game.get("source_system") or "",
                        "reason": game.get("missing_roms") or "",
                    }
                    if status == "bios_required" and not item["reason"]:
                        item["reason"] = f"필수 BIOS {game.get('needed_bios') or '알 수 없음'} 누락"
                    elif status == "chd_required" and not item["reason"]:
                        item["reason"] = "필수 CHD/디스크 이미지가 없습니다."
                    elif status == "incomplete" and not item["reason"]:
                        item["reason"] = "M3U/CUE/GDI 등에서 참조하는 파일이 누락되었습니다."
                    elif status == "missing_file" and not item["reason"]:
                        item["reason"] = "등록된 ROM 파일을 찾을 수 없습니다."
                    elif status == "path_mismatch" and not item["reason"]:
                        item["reason"] = "DB 경로와 실제 ROM 위치가 다릅니다."
                    elif status == "unsupported" and not item["reason"]:
                        item["reason"] = "현재 EmulatorJS Stable 코어에서 구동할 수 없습니다."
                    elif status == "unverified" and not item["reason"]:
                        item["reason"] = "rom-analyzer가 충분한 근거로 판정하지 못했습니다."
                    buckets[status].append(item)

                issue_list = (
                    buckets["missing_file"] + buckets["path_mismatch"] +
                    buckets["bios_required"] + buckets["incomplete"]
                )
                with _HEALTH_PROGRESS_LOCK:
                    progress = dict(_HEALTH_PROGRESS)
                return {
                    "success": True,
                    "summary": {
                        "total": len(all_games),
                        "pass": len(buckets["pass"]),
                        "issues": len(issue_list),
                        "bios": len(buckets["bios_required"]),
                        "incomplete": len(buckets["incomplete"]),
                        "missing_file": len(buckets["missing_file"]),
                        "path_mismatch": len(buckets["path_mismatch"]),
                        "chd": len(buckets["chd_required"]),
                        "unsupported": len(buckets["unsupported"]),
                        "unverified": len(buckets["unverified"]),
                        "reclassify": len(buckets["reclassify_required"]),
                    },
                    "issue_list": issue_list,
                    "missing_file_list": buckets["missing_file"],
                    "path_mismatch_list": buckets["path_mismatch"],
                    "chd_list": buckets["chd_required"],
                    "unsupported_list": buckets["unsupported"],
                    "unverified_list": buckets["unverified"],
                    "reclassify_list": buckets["reclassify_required"],
                    "progress": progress,
                }

            elif action == "scan_roms":
                res = self._run_library_sync("sync")
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

            elif action == "record_boot":
                game_id = str(request.args.get("game_id", "") or "").strip()
                if not game_id:
                    return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}
                return self._record_game_boot(game_id, user_id)

            elif action == "set_play_status":
                game_id = str(request.args.get("game_id", "") or "").strip()
                status = str(request.args.get("status", "") or "").strip().lower()
                if not game_id:
                    return {"success": False, "error": "game_id 파라미터가 누락되었습니다."}
                return self._set_game_play_status(game_id, user_id, status)

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
                return self._request_game_deletion(game_id, requesting_user_id=user_id)

            elif action == "cancel_delete_game":
                game_id = request.args.get("game_id", "")
                return self._cancel_game_deletion(game_id)

            elif action == "delete_queue_status":
                rows = self._db_query(
                    "SELECT id, title, filename, deletion_status, deletion_requested_at, deletion_error "
                    "FROM games WHERE COALESCE(deletion_status, 'active') != 'active' "
                    "ORDER BY COALESCE(deletion_requested_at, ''), id"
                )
                return {"success": True, "count": len(rows), "items": rows}

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
                threading.Thread(target=self._run_library_sync_background, args=("sync",), daemon=True).start()
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
                    variant = self._ensure_cover_variants(game_id, force=True)
                    return {"success": True, "message": "커버 이미지가 성공적으로 변경되었습니다.", "cover_path": dest_path, "cover_variant": variant}
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
