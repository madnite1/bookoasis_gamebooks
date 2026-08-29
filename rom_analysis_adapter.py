# -*- coding: utf-8 -*-
"""rom-analyzer 결과를 기존 Game Books ROM 정보 계약으로 변환하는 경계 계층."""

import json
import os
from pathlib import Path


GAMEBOOKS_CORE_MAP = {
    "nes": "nes",
    "fds": "nes",
    "snes": "snes",
    "gb": "gb",
    "gbc": "gbc",
    "gba": "gba",
    "nds": "nds",
    "n64": "n64",
    "vb": "vb",
    "megadrive": "segaMD",
    "mastersystem": "segaMS",
    "gamegear": "segaGG",
    "sega32x": "sega32x",
    "segacd": "segaCD",
    "saturn": "saturn",
    "dreamcast": "dreamcast",
    "psx": "psx",
    "psp": "psp",
    "pce": "pce",
    "pcecd": "pce",
    "supergrafx": "pce",
    "pcfx": "pcfx",
    "ngp": "ngp",
    "ngpc": "ngp",
    "wonderswan": "ws",
    "wsc": "ws",
    "atari2600": "atari2600",
    "atari5200": "atari5200",
    "atari7800": "atari7800",
    "lynx": "lynx",
    "jaguar": "jaguar",
    "coleco": "coleco",
    "arcade": "arcade",
}

GAMEBOOKS_PLATFORM_MAP = {
    "nes": "NES",
    "fds": "FDS",
    "snes": "SNES",
    "gb": "GB",
    "gbc": "GBC",
    "gba": "GBA",
    "nds": "NDS",
    "n64": "N64",
    "vb": "VirtualBoy",
    "megadrive": "Genesis",
    "mastersystem": "MasterSystem",
    "gamegear": "GameGear",
    "sega32x": "Sega32X",
    "segacd": "SegaCD",
    "saturn": "Saturn",
    "dreamcast": "Dreamcast",
    "psx": "PS1",
    "psp": "PSP",
    "pce": "PCE",
    "pcecd": "PCECD",
    "supergrafx": "SuperGrafx",
    "pcfx": "PC-FX",
    "ngp": "NGP",
    "ngpc": "NGPC",
    "wonderswan": "WonderSwan",
    "wsc": "WonderSwanColor",
    "atari2600": "Atari2600",
    "atari5200": "Atari5200",
    "atari7800": "Atari7800",
    "lynx": "Lynx",
    "jaguar": "Jaguar",
    "coleco": "ColecoVision",
    "arcade": "Arcade",
}


def _load_analyzer():
    from rom_analyzer import analyze, __version__
    return analyze, __version__


def is_analyzer_available():
    try:
        _load_analyzer()
        return True
    except Exception:
        return False


def get_vendor_info():
    try:
        import rom_analyzer
        metadata_path = Path(rom_analyzer.__file__).resolve().parent / "VENDORED_FROM.json"
        if metadata_path.is_file():
            return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _normalize_archive_name(value):
    name = str(value or "").strip()
    if not name:
        return ""
    if os.path.splitext(name)[1]:
        return name
    return name + ".zip"


def _resolve_referenced_files(file_path, references):
    base_dir = os.path.dirname(os.path.abspath(str(file_path)))
    resolved = []
    for reference in references or []:
        ref = str(reference or "").strip()
        if not ref:
            continue
        candidate = ref if os.path.isabs(ref) else os.path.abspath(os.path.join(base_dir, ref))
        if os.path.isfile(candidate) and candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _convert_result(result):
    system_id = str(getattr(result, "system_id", "") or "").lower()
    arcade = getattr(result, "arcade_info", None)
    disc = getattr(result, "disc_info", None)
    bios = getattr(result, "bios_info", None)
    header = getattr(result, "header_metadata", None)
    emulatorjs = getattr(result, "emulatorjs", None)

    core = GAMEBOOKS_CORE_MAP.get(system_id, "")
    platform = GAMEBOOKS_PLATFORM_MAP.get(system_id, "")
    if getattr(result, "is_arcade", False):
        core = "arcade"
        if arcade and (getattr(arcade, "required_bios", None) == ["neogeo.zip"] or "neogeo.zip" in (getattr(arcade, "required_bios", None) or [])):
            platform = "Neo-Geo"
        else:
            platform = "Arcade"

    bios_files = list(getattr(bios, "bios_files", None) or [])
    required_bios = list(getattr(arcade, "required_bios", None) or [])
    needed_bios = (required_bios or bios_files or [""])[0]

    parent_rom = _normalize_archive_name(getattr(arcade, "parent_rom", "") if arcade else "")
    required_chd = str(getattr(arcade, "chd_name", "") or "") if arcade else ""
    referenced = list(getattr(disc, "referenced_files", None) or []) if disc else []
    missing = list(getattr(disc, "missing_files", None) or []) if disc else []
    resolved = _resolve_referenced_files(getattr(result, "file_path", ""), referenced)

    evidence = list(getattr(result, "detection_methods", None) or [])
    source_system = evidence[0] if evidence else "rom_analyzer"
    title = str(getattr(header, "title", "") or "") if header else ""
    serial = str(getattr(header, "serial", "") or "") if header else ""

    return {
        "core": core,
        "platform": platform,
        "title": title,
        "game_code": serial or (str(getattr(arcade, "driver", "") or "") if arcade else ""),
        "maker_code": "",
        "needed_bios": needed_bios,
        "parent_hint": parent_rom,
        "required_chd": required_chd,
        "matched_count": int(getattr(arcade, "matched_count", 0) or 0) if arcade else 0,
        "total_roms": int(getattr(arcade, "total_roms", 0) or 0) if arcade else 0,
        "match_rate": float(getattr(arcade, "match_rate", 0.0) or 0.0) if arcade else 0.0,
        "serial_code": serial,
        "source_system": source_system,
        "metadata_source": "rom-analyzer",
        "metadata_confidence": int(round(float(getattr(result, "confidence_score", 0.0) or 0.0) * 100)),
        "disk_missing_files": missing,
        "resolved_disk_files": resolved,
        "disc_count": int(getattr(disc, "disc_count", 0) or getattr(disc, "track_count", 0) or 1) if disc else 1,
        "identity_status": str(getattr(result, "identity_status", "") or ""),
        "analysis_methods": evidence,
        "analysis_warnings": list(getattr(result, "warnings", None) or []),
        "analysis_conflicts": list(getattr(result, "conflicts", None) or []),
        "is_playable": bool(getattr(result, "is_playable", False)),
        "emulatorjs_supported": bool(getattr(emulatorjs, "supported", False)) if emulatorjs else False,
        "emulatorjs_core": str(getattr(emulatorjs, "core", "") or "") if emulatorjs else "",
        "emulatorjs_system": str(getattr(emulatorjs, "system", "") or "") if emulatorjs else "",
    }


def analyze_rom(file_path, compute_hashes=False):
    """rom-analyzer로 분석하고 기존 Game Books dict 계약으로 변환한다."""
    analyze, _version = _load_analyzer()
    return _convert_result(analyze(file_path, compute_hashes=compute_hashes))
