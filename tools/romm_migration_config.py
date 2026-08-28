from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
SOURCE_ROOT = Path("/mnt/gdrive/emulatorjs")
SOURCE_ROMS_DIR = SOURCE_ROOT / "roms"
SOURCE_BIOS_DIR = SOURCE_ROOT / "bios"
SOURCE_COVERS_DIR = SOURCE_ROOT / "covers"

TARGET_ROMM_ROOT = SOURCE_ROOT / "romm_library"
TARGET_LIBRARY_DIR = TARGET_ROMM_ROOT / "library"
TARGET_RESOURCES_DIR = TARGET_ROMM_ROOT / "resources"
TARGET_ASSETS_DIR = TARGET_ROMM_ROOT / "assets"
TARGET_RESOURCES_ROMS_DIR = TARGET_RESOURCES_DIR / "roms"

MANIFEST_DIR = ROOT_DIR / ".hermes_migration"
MANIFEST_PATH = MANIFEST_DIR / "migration_manifest.json"
UNRESOLVED_PATH = MANIFEST_DIR / "migration_unresolved.json"
SUMMARY_PATH = MANIFEST_DIR / "migration_summary.txt"
COVER_MAP_PATH = MANIFEST_DIR / "migration_cover_map.json"
BIOS_MAP_PATH = MANIFEST_DIR / "migration_bios_map.json"
SLUG_STATS_PATH = MANIFEST_DIR / "migration_slug_stats.json"
CANCEL_FLAG_PATH = MANIFEST_DIR / ".migration_cancelled"
STATUS_PATH = ROOT_DIR / "tools" / ".romm_migration_status.json"

STANDARD_SLUGS = (
    "arcade",
    "coleco",
    "dreamcast",
    "gamegear",
    "gba",
    "genesis",
    "n64",
    "nes",
    "pce",
    "psx",
    "saturn",
    "sms",
    "snes",
    "unknown",
)

SOURCE_TOP_DIR_SLUG_HINTS = {
    "n64": "n64",
    "arcade": "arcade",
    "coleco": "coleco",
    "gamegear": "gamegear",
    "gba": "gba",
    "mame2003": "arcade",
    "megadriv": "genesis",
    "nes": "nes",
    "psx": "psx",
    "segamd": "genesis",
    "snes": "snes",
}

BIOS_FILENAME_MAP = {
    "scph1001.bin": "psx",
    "scph5500.bin": "psx",
    "scph5501.bin": "psx",
    "scph5502.bin": "psx",
    "saturn_bios.bin": "saturn",
    "dc_boot.bin": "dreamcast",
    "dc_flash.bin": "dreamcast",
    "syscard3.pce": "pce",
    "disksys.rom": "nes",
    "gba_bios.bin": "gba",
    "neogeo.zip": "arcade",
    "pgm.zip": "arcade",
    "acpsx.zip": "arcade",
}

DISK_BUNDLE_EXTS = {".cue", ".gdi", ".m3u", ".bin", ".img", ".iso", ".ccd", ".sub", ".mds", ".chd", ".pbp"}


@dataclass(frozen=True)
class MigrationPaths:
    source_root: Path = SOURCE_ROOT
    source_roms_dir: Path = SOURCE_ROMS_DIR
    source_bios_dir: Path = SOURCE_BIOS_DIR
    source_covers_dir: Path = SOURCE_COVERS_DIR
    target_root: Path = TARGET_ROMM_ROOT
    target_library_dir: Path = TARGET_LIBRARY_DIR
    target_resources_dir: Path = TARGET_RESOURCES_DIR
    target_assets_dir: Path = TARGET_ASSETS_DIR
    target_resources_roms_dir: Path = TARGET_RESOURCES_ROMS_DIR
    manifest_dir: Path = MANIFEST_DIR


def ensure_manifest_dir() -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    return MANIFEST_DIR


def get_target_rom_dir(slug: str) -> Path:
    return TARGET_LIBRARY_DIR / slug / "roms"


def get_target_bios_dir(slug: str) -> Path:
    return TARGET_LIBRARY_DIR / slug / "bios"


def get_target_cover_dir(rom_id: str) -> Path:
    return TARGET_RESOURCES_ROMS_DIR / str(rom_id) / "cover"
