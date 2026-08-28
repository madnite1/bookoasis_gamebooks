from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from bookoasis_gamebooks import _detect_rom_info, _is_bios_file, KNOWN_BIOS_STEMS
except ImportError:
    from plugins.metadata.bookoasis_gamebooks.bookoasis_gamebooks import _detect_rom_info, _is_bios_file, KNOWN_BIOS_STEMS

from .romm_bundle_resolver import collect_bundle
from .romm_migration_config import (
    BIOS_FILENAME_MAP,
    BIOS_MAP_PATH,
    COVER_MAP_PATH,
    MANIFEST_PATH,
    SLUG_STATS_PATH,
    SOURCE_BIOS_DIR,
    SOURCE_COVERS_DIR,
    SOURCE_ROMS_DIR,
    SUMMARY_PATH,
    TARGET_RESOURCES_DIR,
    TARGET_ROMM_ROOT,
    UNRESOLVED_PATH,
    ensure_manifest_dir,
    get_target_bios_dir,
    get_target_cover_dir,
    get_target_rom_dir,
)
from .romm_migration_apply import is_migration_cancelled
from .romm_slug_map import resolve_target_slug

ALLOWED_SCAN_EXTS = {
    ".gba", ".gb", ".gbc", ".sfc", ".smc", ".snes", ".fig", ".nes", ".fds", ".unf", ".unif",
    ".nds", ".n64", ".z64", ".v64", ".vb", ".vboy", ".md", ".gen", ".smd", ".sms", ".gg", ".sg",
    ".32x", ".psx", ".ps1", ".pbp", ".cso", ".pce", ".sgx", ".pcfx", ".ngp", ".ngc", ".ws", ".wsc",
    ".a26", ".a52", ".a78", ".lnx", ".j64", ".jag", ".col", ".adf", ".d64", ".zip", ".7z", ".cue", ".gdi", ".bin", ".iso", ".img", ".chd",
}

SKIP_DIR_NAMES = {".git", ".hermes", ".hermes_migration", "__pycache__", "romm_library"}


def _safe_rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except Exception:
        return str(path)


def _iter_source_rom_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(SOURCE_ROMS_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith('.')]
        for name in sorted(names):
            if name.startswith('.'):
                continue
            path = Path(root) / name
            if path.suffix.lower() in ALLOWED_SCAN_EXTS:
                files.append(path)
    return sorted(files)


def is_remote_mount(path: Path | str) -> bool:
    """원격 마운트(fuse, rclone, nfs, cifs, smb 등) 여부 확인"""
    try:
        p_str = str(Path(path).resolve())
        if not os.path.exists("/proc/self/mountinfo"):
            return False
        with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(" - ")
                if len(parts) == 2:
                    mount_part = parts[0].split()
                    fs_part = parts[1].split()
                    if len(mount_part) >= 5 and len(fs_part) >= 1:
                        mount_point = mount_part[4]
                        fstype = fs_part[0].lower()
                        if p_str == mount_point or p_str.startswith(mount_point.rstrip("/") + "/"):
                            if any(k in fstype for k in ("fuse", "rclone", "nfs", "cifs", "smb")):
                                return True
    except Exception:
        pass
    return False


def _is_remote_mount(path: Path) -> bool:
    """하위 호환용 래퍼"""
    return is_remote_mount(path)


def _load_gba_db_game_cache() -> dict[str, dict[str, Any]]:
    """원격 마운트 환경일 때 gba.db에서 분석된 롬 메타데이터 캐시 로드"""
    cache: dict[str, dict[str, Any]] = {}
    candidates = [
        os.environ.get("PLUGIN_DATA_DIR"),
        str(ROOT_DIR.parent.parent / "data" / "bookoasis_gamebooks"),
        "/app/plugins/data/bookoasis_gamebooks",
    ]
    db_path = None
    for cand in candidates:
        if cand:
            p = Path(cand) / "gba.db"
            if p.is_file():
                db_path = p
                break
    if not db_path:
        return cache

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM games")
        rows = cur.fetchall()
        for r in rows:
            d = dict(r)
            fp = d.get("file_path") or ""
            fn = d.get("filename") or ""
            if fp:
                norm_p = os.path.normpath(fp)
                cache[norm_p] = d
                # source_roms 기준 상대 경로 인덱싱
                parts = fp.replace("\\", "/").split("/")
                for i, part in enumerate(parts):
                    if part.lower() == "roms" and i + 1 < len(parts):
                        rel = "/".join(parts[i + 1:])
                        cache[f"rel::{rel}"] = d
                        break
            if fn:
                cache[f"fn::{fn}"] = d
        conn.close()
    except Exception:
        pass
    return cache


def _normalize_detect_info(info: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(info)
    normalized.setdefault("hash_basis", {})
    normalized["hash_basis"] = {
        "lookup_kind": "unimplemented",
        "crc32": None,
        "md5": None,
        "sha1": None,
        "redump_match": False,
        "ra_match": False,
        "dat_match": bool(info.get("matched_count")),
    }
    return normalized


def _detect_rom_info_with_cache(path: Path, db_cache: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """원격 마운트 시 gba.db 캐시 우선 활용, 없거나 로컬이면 실시간 검사"""
    if db_cache:
        norm_p = os.path.normpath(str(path))
        cached = db_cache.get(norm_p)
        if not cached:
            try:
                rel = str(path.relative_to(SOURCE_ROMS_DIR))
                cached = db_cache.get(f"rel::{rel}")
            except Exception:
                pass
        if not cached:
            cached = db_cache.get(f"fn::{path.name}")

        if cached:
            return {
                "core": cached.get("core") or "",
                "platform": cached.get("platform") or "",
                "title": cached.get("title") or path.stem,
                "game_code": cached.get("game_code") or "",
                "maker_code": cached.get("maker_code") or "",
                "needed_bios": cached.get("needed_bios") or "",
                "parent_hint": "",
                "required_chd": "",
                "matched_count": 1 if cached.get("platform") else 0,
                "total_roms": 1,
                "match_rate": 1.0,
                "serial_code": cached.get("serial_code") or "",
                "source_system": cached.get("source_system") or "gba_db_cache",
                "metadata_source": cached.get("metadata_source") or "gba_db",
                "metadata_confidence": cached.get("metadata_confidence") or 90,
                "disk_missing_files": [],
                "resolved_disk_files": [],
                "disc_count": int(cached.get("disc_number") or 1),
                "hash_basis": {
                    "lookup_kind": "gba_db",
                    "crc32": cached.get("rom_crc32") or None,
                    "md5": cached.get("rom_md5") or None,
                    "sha1": cached.get("rom_sha1") or None,
                    "redump_match": bool(cached.get("rom_crc32")),
                    "ra_match": False,
                    "dat_match": bool(cached.get("rom_crc32")),
                },
            }

    stem_lower = path.stem.lower()
    if stem_lower in KNOWN_BIOS_STEMS:
        return {
            "core": "arcade" if path.suffix.lower() == ".zip" else "",
            "platform": "arcade" if path.suffix.lower() == ".zip" else "",
            "title": path.stem,
            "needed_bios": "",
            "matched_count": 0,
            "total_roms": 0,
            "match_rate": 0.0,
            "source_system": "bios_stem",
            "metadata_source": "known_bios",
            "metadata_confidence": 100,
            "disk_missing_files": [],
            "resolved_disk_files": [],
            "disc_count": 1,
            "hash_basis": {"lookup_kind": "bios_stem", "crc32": None, "md5": None, "sha1": None, "redump_match": False, "ra_match": False, "dat_match": False},
        }

    # 로컬 디스크이거나 DB 캐시에 없는 신규 파일은 실시간 파일 헤더/DAT 분석
    return _detect_rom_info(str(path))


def _build_manifest_entry(path: Path, migration_index: int, db_cache: dict[str, dict[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source_top_dir = path.relative_to(SOURCE_ROMS_DIR).parts[0] if len(path.relative_to(SOURCE_ROMS_DIR).parts) > 1 else ""
    bundle = collect_bundle(path)
    info = _normalize_detect_info(_detect_rom_info_with_cache(path, db_cache=db_cache))
    decision = resolve_target_slug(info, source_top_dir=source_top_dir, file_path=str(path))

    target_rom_dir = get_target_rom_dir(decision.slug)
    bundle_target_paths = [target_rom_dir / Path(p).name for p in bundle["bundle_files"]]
    target_rel_dir = _safe_rel(target_rom_dir, TARGET_ROMM_ROOT)
    entry = {
        "migration_id": f"mig_{decision.slug}_{migration_index:06d}",
        "source_root": str(SOURCE_ROMS_DIR),
        "source_path": str(path),
        "source_rel_path": _safe_rel(path, SOURCE_ROMS_DIR),
        "source_top_dir": source_top_dir,
        "bundle_type": bundle["bundle_type"],
        "bundle_files": bundle["bundle_files"],
        "bundle_missing_files": bundle["bundle_missing_files"],
        "detected_platform": info.get("platform") or "",
        "detected_core": info.get("core") or "",
        "detected_by": decision.detected_by,
        "confidence": decision.confidence,
        "serial_code": info.get("serial_code") or "",
        "hash_basis": info.get("hash_basis") or {},
        "title_guess": info.get("title") or path.stem,
        "target_platform_slug": decision.slug,
        "target_rom_dir": str(target_rom_dir),
        "target_rel_dir": target_rel_dir,
        "bundle_source_rel_paths": [_safe_rel(Path(p), SOURCE_ROMS_DIR) for p in bundle["bundle_files"]],
        "bundle_target_rel_paths": [_safe_rel(p, TARGET_ROMM_ROOT) for p in bundle_target_paths],
        "target_paths": [str(p) for p in bundle_target_paths],
        "copy_group_key": f"rom::{decision.slug}::{target_rel_dir}",
        "rewrite_descriptor": bundle["rewrite_descriptor"],
        "copy_mode": "copy",
        "status": "planned",
        "notes": decision.reasons,
        "needed_bios": info.get("needed_bios") or "",
        "source_system": info.get("source_system") or "",
    }

    unresolved = None
    if decision.slug == "unknown" or bundle["bundle_missing_files"]:
        unresolved = {
            "source_path": str(path),
            "problem_type": "bundle_missing_files" if bundle["bundle_missing_files"] else "platform_ambiguous",
            "candidate_platforms": [info.get("platform") or "", info.get("core") or ""],
            "detected_by": decision.detected_by,
            "confidence": decision.confidence,
            "missing_files": bundle["bundle_missing_files"],
            "reason": "; ".join(decision.reasons) or "unknown",
        }
    return entry, unresolved


def _safe_resolve_str(p: str | Path) -> str:
    try:
        return os.path.normpath(str(p))
    except Exception:
        return str(p)


def _build_bios_map() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not os.path.isdir(str(SOURCE_BIOS_DIR)):
        return entries
    target_cache: dict[str, str] = {}
    try:
        filenames = sorted(os.listdir(str(SOURCE_BIOS_DIR)))
    except Exception:
        filenames = []
    for fname in filenames:
        if fname.startswith('.'):
            continue
        path = SOURCE_BIOS_DIR / fname
        lower = fname.lower()
        stem = Path(fname).stem.lower()
        slug = BIOS_FILENAME_MAP.get(lower)
        detected_by = []
        confidence = 0.99 if slug else 0.0
        if slug:
            detected_by.append("known_filename")
        elif stem in KNOWN_BIOS_STEMS or "bios" in stem or stem in ("boardrom", "bootrom", "sysrom", "firmware"):
            slug = "arcade" if fname.lower().endswith(".zip") else "unknown"
            detected_by.append("bios_name_hint")
            confidence = 0.65 if slug == "arcade" else 0.4
        else:
            slug = "unknown"
            detected_by.append("unresolved")
            confidence = 0.2
        target_dir = target_cache.get(slug)
        if not target_dir:
            target_dir = str(get_target_bios_dir(slug))
            target_cache[slug] = target_dir
        target_rel_dir = _safe_rel(Path(target_dir), TARGET_ROMM_ROOT)
        entries.append({
            "source_bios": str(path),
            "source_name": fname,
            "source_stem": stem,
            "detected_platform": slug,
            "detected_by": detected_by,
            "confidence": round(confidence, 4),
            "target_bios_dir": target_dir,
            "target_rel_dir": target_rel_dir,
            "target_path": f"{target_dir}/{fname}",
            "source_rel_path": _safe_rel(path, SOURCE_BIOS_DIR),
            "target_rel_path": f"{target_rel_dir}/{fname}",
            "copy_group_key": f"bios::{slug}::{target_rel_dir}",
            "status": "planned",
        })
    return entries


def _build_cover_map(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not os.path.isdir(str(SOURCE_COVERS_DIR)):
        return entries

    title_index: dict[str, dict[str, Any]] = {}
    for item in manifest:
        title = (item.get("title_guess") or "").lower().replace(" ", "_")
        if title:
            title_index.setdefault(title, item)
        source_stem = Path(item.get("source_path") or "").stem.lower().replace(" ", "_")
        if source_stem:
            title_index.setdefault(source_stem, item)

    next_rom_id = 100000
    target_cover_root = str(TARGET_RESOURCES_DIR / "roms")
    try:
        cover_filenames = sorted(os.listdir(str(SOURCE_COVERS_DIR)))
    except Exception:
        cover_filenames = []

    for fname in cover_filenames:
        if fname.startswith('.'):
            continue
        path = SOURCE_COVERS_DIR / fname
        cover_stem = Path(fname).stem.lower()
        cover_key = cover_stem.replace(" ", "_")
        matched = title_index.get(cover_key) or title_index.get(cover_stem)
        
        target_dir = f"{target_cover_root}/{next_rom_id}/cover"
        ext = Path(fname).suffix.lower()
        target_rel_dir = _safe_rel(Path(target_dir), TARGET_ROMM_ROOT)
        target_cover_p = f"{target_dir}/original{ext}"
        entry = {
            "source_cover": str(path),
            "cover_name": fname,
            "matched_migration_id": matched.get("migration_id") if matched else None,
            "matched_rom_source": matched.get("source_path") if matched else None,
            "match_basis": ["exact_match"] if matched else ["unmatched"],
            "target_resource_dir": target_dir,
            "target_rel_dir": target_rel_dir,
            "target_cover_path": target_cover_p,
            "source_rel_path": _safe_rel(path, SOURCE_COVERS_DIR),
            "target_rel_path": f"{target_rel_dir}/original{ext}",
            "copy_group_key": f"cover::{matched.get('target_platform_slug', 'unknown') if matched else 'unmatched'}::{target_rel_dir}",
            "status": "planned" if matched else "unresolved",
        }
        if matched:
            next_rom_id += 1
        entries.append(entry)
    return entries


def _build_slug_stats(manifest: list[dict[str, Any]]) -> dict[str, Any]:
    source_top_dirs: dict[str, Counter] = defaultdict(Counter)
    for item in manifest:
        source_top_dirs[item.get("source_top_dir") or ""] [item.get("target_platform_slug") or "unknown"] += 1
    return {
        "source_top_dirs": {k: dict(v) for k, v in sorted(source_top_dirs.items())},
    }


def _build_summary(manifest: list[dict[str, Any]], unresolved: list[dict[str, Any]], bios_map: list[dict[str, Any]], cover_map: list[dict[str, Any]]) -> str:
    slug_counter = Counter(item.get("target_platform_slug") or "unknown" for item in manifest)
    lines = [
        f"target_root: {TARGET_ROMM_ROOT}",
        f"source_roms: {SOURCE_ROMS_DIR}",
        f"source_bios: {SOURCE_BIOS_DIR}",
        f"source_covers: {SOURCE_COVERS_DIR}",
        f"rom_count: {len(manifest)}",
        f"unresolved_count: {len(unresolved)}",
        f"bios_count: {len(bios_map)}",
        f"cover_count: {len(cover_map)}",
        "slug_counts:",
    ]
    for slug, count in sorted(slug_counter.items()):
        lines.append(f"  - {slug}: {count}")
    return "\n".join(lines) + "\n"


def build_dry_run_plan(progress_cb: Any | None = None) -> dict[str, Any]:
    ensure_manifest_dir()
    
    # 원격/클라우드 마운트(fuse/rclone/nfs 등)인 경우에만 gba.db 메타데이터 캐시 활용
    is_remote = _is_remote_mount(SOURCE_ROMS_DIR)
    db_cache: dict[str, dict[str, Any]] | None = _load_gba_db_game_cache() if is_remote else None
    
    if progress_cb:
        detail_msg = "원격 마운트 감지됨: gba.db 메타데이터 캐시 연동" if is_remote and db_cache else "로컬 디스크 모드: 실시간 파일 분석 수행"
        progress_cb({
            "phase": "planning",
            "percent": 0,
            "current": 0,
            "total": 0,
            "current_item": "ROM 소스 파일 목록 검색 중...",
            "details": detail_msg,
        })
    files = _iter_source_rom_files()
    total_files = len(files)
    files_by_name = {f.name for f in files}
    manifest: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen_bundle_roots: set[str] = set()

    for index, path in enumerate(files, start=1):
        if is_migration_cancelled():
            raise InterruptedError("Migration cancelled by user during dry-run planning.")
        if progress_cb and (index % 5 == 1 or index == total_files):
            pct = int((index / max(1, total_files)) * 100)
            progress_cb({
                "phase": "planning",
                "percent": pct,
                "current": index,
                "total": total_files,
                "current_item": f"ROM 분석 중 ({index}/{total_files}): {path.name}",
                "details": f"메타데이터 및 구조 계획 수립 중 ({pct}%)",
            })
        ext = path.suffix.lower()
        if ext in {".bin", ".img", ".iso", ".ccd", ".sub", ".mds"}:
            cue = path.with_suffix('.cue')
            gdi = path.with_suffix('.gdi')
            if cue.name in files_by_name or gdi.name in files_by_name:
                continue
        entry, unresolved_entry = _build_manifest_entry(path, index, db_cache=db_cache)
        bundle_paths = [_safe_resolve_str(p) for p in entry["bundle_files"]]
        if any(p in seen_bundle_roots for p in bundle_paths):
            continue
        for p in bundle_paths:
            seen_bundle_roots.add(p)
        manifest.append(entry)
        if unresolved_entry:
            unresolved.append(unresolved_entry)

    if progress_cb:
        progress_cb({
            "phase": "planning",
            "percent": 95,
            "current": total_files,
            "total": total_files,
            "current_item": "BIOS 및 커버 인덱스 맵 생성 중...",
            "details": "BIOS/커버 매핑 분석 중",
        })

    bios_map = _build_bios_map()
    cover_map = _build_cover_map(manifest)
    slug_stats = _build_slug_stats(manifest)
    summary_text = _build_summary(manifest, unresolved, bios_map, cover_map)

    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    UNRESOLVED_PATH.write_text(json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8")
    BIOS_MAP_PATH.write_text(json.dumps(bios_map, ensure_ascii=False, indent=2), encoding="utf-8")
    COVER_MAP_PATH.write_text(json.dumps(cover_map, ensure_ascii=False, indent=2), encoding="utf-8")
    SLUG_STATS_PATH.write_text(json.dumps(slug_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    SUMMARY_PATH.write_text(summary_text, encoding="utf-8")

    return {
        "manifest_path": str(MANIFEST_PATH),
        "unresolved_path": str(UNRESOLVED_PATH),
        "bios_map_path": str(BIOS_MAP_PATH),
        "cover_map_path": str(COVER_MAP_PATH),
        "slug_stats_path": str(SLUG_STATS_PATH),
        "summary_path": str(SUMMARY_PATH),
        "rom_count": len(manifest),
        "unresolved_count": len(unresolved),
        "bios_count": len(bios_map),
        "cover_count": len(cover_map),
    }


if __name__ == "__main__":
    print(json.dumps(build_dry_run_plan(), ensure_ascii=False, indent=2))
