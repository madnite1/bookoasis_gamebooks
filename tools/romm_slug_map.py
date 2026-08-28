from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
# Resolve both deployed layout (<project>/plugins/metadata/bookoasis_gamebooks)
# and development layout (<workspace>/BookOasis_plugins/bookoasis_gamebooks).
_PROJECT_CANDIDATES = []
if ROOT_DIR.parent.name == "metadata" and ROOT_DIR.parent.parent.name == "plugins":
    _PROJECT_CANDIDATES.append(ROOT_DIR.parent.parent.parent)
_PROJECT_CANDIDATES.extend([
    ROOT_DIR.parent.parent / "BookOasis",
    Path("/app"),
])
PROJECT_ROOT = next(
    (p for p in _PROJECT_CANDIDATES if (p / "plugins" / "metadata" / "base.py").is_file()),
    _PROJECT_CANDIDATES[0],
)
for _import_root in (PROJECT_ROOT, ROOT_DIR):
    _root_s = str(_import_root)
    if _root_s not in sys.path:
        sys.path.insert(0, _root_s)

from .romm_migration_config import SOURCE_TOP_DIR_SLUG_HINTS

PLATFORM_TO_SLUG = {
    "Arcade": "arcade",
    "C64": "unknown",
    "ColecoVision": "coleco",
    "Dreamcast": "dreamcast",
    "FDS": "nes",
    "GB": "unknown",
    "GBA": "gba",
    "GBC": "unknown",
    "GameGear": "gamegear",
    "Genesis": "genesis",
    "Lynx": "unknown",
    "MasterSystem": "sms",
    "N64": "n64",
    "NES": "nes",
    "NGP": "unknown",
    "NGPC": "unknown",
    "Neo-Geo": "arcade",
    "PCE": "pce",
    "PCECD": "pce",
    "PS1": "psx",
    "PSP": "unknown",
    "Saturn": "saturn",
    "SG-1000": "sms",
    "SNES": "snes",
    "Sega32X": "genesis",
    "SuperGrafx": "pce",
    "VirtualBoy": "unknown",
    "WonderSwan": "unknown",
    "WonderSwanColor": "unknown",
}

CORE_TO_SLUG = {
    "arcade": "arcade",
    "coleco": "coleco",
    "dreamcast": "dreamcast",
    "gba": "gba",
    "mame2003": "arcade",
    "n64": "n64",
    "nes": "nes",
    "pce": "pce",
    "psx": "psx",
    "saturn": "saturn",
    "segaGG": "gamegear",
    "segaMD": "genesis",
    "segaMS": "sms",
    "snes": "snes",
}

DISK_EXT_TO_SLUG = {
    ".gdi": "dreamcast",
    ".pbp": "psx",
}


@dataclass
class SlugDecision:
    slug: str
    confidence: float
    detected_by: list[str]
    reasons: list[str]


def normalize_source_top_dir(name: str) -> str:
    key = (name or "").strip().lower()
    if not key:
        return ""
    return SOURCE_TOP_DIR_SLUG_HINTS.get(key, key)


def _append_reason(reasons: list[str], value: str) -> None:
    if value and value not in reasons:
        reasons.append(value)


def _slug_from_platform(platform: str) -> str:
    return PLATFORM_TO_SLUG.get((platform or "").strip(), "")


def _slug_from_core(core: str) -> str:
    return CORE_TO_SLUG.get((core or "").strip(), "")


def _score_for_detected_by(detected_by: Iterable[str], has_hash: bool, has_serial: bool, source_hint_match: bool) -> float:
    methods = set(detected_by)
    if has_hash:
        return 0.96 if source_hint_match else 0.93
    if has_serial and "descriptor" in methods:
        return 0.88
    if has_serial:
        return 0.84
    if "descriptor" in methods and source_hint_match:
        return 0.76
    if "header" in methods and source_hint_match:
        return 0.74
    if "platform" in methods or "core" in methods:
        return 0.68 if source_hint_match else 0.61
    if "folder_hint" in methods:
        return 0.58
    return 0.35


def resolve_target_slug(rom_info: dict, source_top_dir: str, file_path: str) -> SlugDecision:
    detected_by: list[str] = []
    reasons: list[str] = []
    candidates: list[str] = []

    normalized_hint = normalize_source_top_dir(source_top_dir)
    ext = Path(file_path).suffix.lower()

    hash_basis = rom_info.get("hash_basis") or {}
    has_hash = bool(hash_basis.get("redump_match") or hash_basis.get("ra_match") or hash_basis.get("dat_match"))
    has_serial = bool((rom_info.get("serial_code") or "").strip())

    platform_slug = _slug_from_platform(rom_info.get("platform") or "")
    core_slug = _slug_from_core(rom_info.get("core") or "")

    if has_hash and platform_slug:
        candidates.append(platform_slug)
        detected_by.append("hash")
        _append_reason(reasons, f"hash→platform:{rom_info.get('platform')}")

    if not candidates and has_hash and core_slug:
        candidates.append(core_slug)
        detected_by.append("hash")
        _append_reason(reasons, f"hash→core:{rom_info.get('core')}")

    if has_serial and platform_slug:
        candidates.append(platform_slug)
        detected_by.append("serial")
        _append_reason(reasons, f"serial:{rom_info.get('serial_code')}")

    if not candidates and ext in DISK_EXT_TO_SLUG:
        candidates.append(DISK_EXT_TO_SLUG[ext])
        detected_by.append("descriptor")
        _append_reason(reasons, f"descriptor_ext:{ext}")

    if not candidates and ext == ".cue" and (rom_info.get("resolved_disk_files") or rom_info.get("disk_missing_files")):
        candidates.append("saturn" if normalized_hint == "saturn" else "psx")
        detected_by.append("descriptor")
        _append_reason(reasons, "cue_bundle")

    if not candidates and platform_slug:
        candidates.append(platform_slug)
        detected_by.append("platform")
        _append_reason(reasons, f"platform:{rom_info.get('platform')}")

    if not candidates and core_slug:
        candidates.append(core_slug)
        detected_by.append("core")
        _append_reason(reasons, f"core:{rom_info.get('core')}")

    if normalized_hint in {"psx", "saturn", "dreamcast", "n64", "gba", "snes", "nes", "genesis", "sms", "gamegear", "coleco", "arcade"}:
        if not candidates:
            candidates.append(normalized_hint)
        detected_by.append("folder_hint")
        _append_reason(reasons, f"folder:{source_top_dir}")

    slug = candidates[0] if candidates else "unknown"
    source_hint_match = bool(normalized_hint and normalized_hint == slug)
    confidence = _score_for_detected_by(detected_by, has_hash=has_hash, has_serial=has_serial, source_hint_match=source_hint_match)

    if slug not in {"arcade", "coleco", "dreamcast", "gamegear", "gba", "genesis", "n64", "nes", "pce", "psx", "saturn", "sms", "snes"}:
        slug = "unknown"
        confidence = min(confidence, 0.5)
        _append_reason(reasons, "unsupported_or_ambiguous")

    return SlugDecision(slug=slug, confidence=round(confidence, 4), detected_by=detected_by, reasons=reasons)
