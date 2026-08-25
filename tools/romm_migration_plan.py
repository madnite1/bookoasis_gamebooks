from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bookoasis_gamebooks import _detect_rom_info, _is_bios_file, KNOWN_BIOS_STEMS

from tools.romm_bundle_resolver import collect_bundle
from tools.romm_migration_config import (
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
from tools.romm_slug_map import resolve_target_slug

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


def _build_manifest_entry(path: Path, migration_index: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source_top_dir = path.relative_to(SOURCE_ROMS_DIR).parts[0] if len(path.relative_to(SOURCE_ROMS_DIR).parts) > 1 else ""
    bundle = collect_bundle(path)
    info = _normalize_detect_info(_detect_rom_info(str(path)))
    decision = resolve_target_slug(info, source_top_dir=source_top_dir, file_path=str(path))

    target_rom_dir = get_target_rom_dir(decision.slug)
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
        "target_paths": [str(target_rom_dir / Path(p).name) for p in bundle["bundle_files"]],
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


def _build_bios_map() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not SOURCE_BIOS_DIR.exists():
        return entries
    target_cache: dict[str, str] = {}
    for path in sorted(SOURCE_BIOS_DIR.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        lower = name.lower()
        stem = path.stem.lower()
        slug = BIOS_FILENAME_MAP.get(lower)
        detected_by = []
        confidence = 0.99 if slug else 0.0
        if slug:
            detected_by.append("known_filename")
        elif stem in KNOWN_BIOS_STEMS or "bios" in stem or stem in ("boardrom", "bootrom", "sysrom", "firmware"):
            slug = "arcade" if path.suffix.lower() == ".zip" else "unknown"
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
        entries.append({
            "source_bios": str(path),
            "source_name": name,
            "source_stem": stem,
            "detected_platform": slug,
            "detected_by": detected_by,
            "confidence": round(confidence, 4),
            "target_bios_dir": target_dir,
            "target_path": f"{target_dir}/{name}",
            "status": "planned",
        })
    return entries


def _build_cover_map(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not SOURCE_COVERS_DIR.exists():
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
    for path in sorted(SOURCE_COVERS_DIR.iterdir()):
        if not path.is_file():
            continue
        cover_stem = path.stem.lower()
        cover_key = cover_stem.replace(" ", "_")
        matched = title_index.get(cover_key) or title_index.get(cover_stem)
        
        target_dir = f"{target_cover_root}/{next_rom_id}/cover"
        ext = path.suffix.lower()
        entry = {
            "source_cover": str(path),
            "cover_name": path.name,
            "matched_migration_id": matched.get("migration_id") if matched else None,
            "matched_rom_source": matched.get("source_path") if matched else None,
            "match_basis": ["exact_match"] if matched else ["unmatched"],
            "target_resource_dir": target_dir,
            "target_cover_path": f"{target_dir}/original{ext}",
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


def build_dry_run_plan() -> dict[str, Any]:
    ensure_manifest_dir()
    files = _iter_source_rom_files()
    manifest: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen_bundle_roots: set[str] = set()

    for index, path in enumerate(files, start=1):
        ext = path.suffix.lower()
        if ext in {".bin", ".img", ".iso", ".ccd", ".sub", ".mds"}:
            cue = path.with_suffix('.cue')
            gdi = path.with_suffix('.gdi')
            if cue.exists() or gdi.exists():
                continue
        entry, unresolved_entry = _build_manifest_entry(path, index)
        bundle_paths = [str(Path(p).resolve()) for p in entry["bundle_files"]]
        if any(p in seen_bundle_roots for p in bundle_paths):
            continue
        for p in bundle_paths:
            seen_bundle_roots.add(p)
        manifest.append(entry)
        if unresolved_entry:
            unresolved.append(unresolved_entry)

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
