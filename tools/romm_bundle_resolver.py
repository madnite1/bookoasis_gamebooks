from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bookoasis_gamebooks import (
    _collect_disk_bundle_paths,
    _resolve_disk_sidecars,
    _rewrite_disk_manifest_to_local_paths,
)


def collect_bundle(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path).resolve()
    ext = path.suffix.lower()
    if ext in {".cue", ".gdi", ".bin", ".img", ".iso", ".ccd", ".sub", ".mds"}:
        bundle_files = [str(Path(p)) for p in _collect_disk_bundle_paths(str(path))]
        sidecars = _resolve_disk_sidecars(str(path))
    else:
        bundle_files = [str(path)]
        sidecars = {
            "missing_files": [],
            "resolved_files": [],
            "serial_code": "",
            "disc_count": 1,
        }
    return {
        "bundle_type": ext.lstrip(".") or "file",
        "bundle_files": bundle_files or [str(path)],
        "bundle_missing_files": list(sidecars.get("missing_files") or []),
        "resolved_disk_files": list(sidecars.get("resolved_files") or []),
        "serial_code": sidecars.get("serial_code") or "",
        "disc_count": int(sidecars.get("disc_count") or 1),
        "rewrite_descriptor": ext in {".cue", ".gdi"},
    }


def rewrite_descriptor_local_paths(file_path: str | Path) -> bool:
    return bool(_rewrite_disk_manifest_to_local_paths(str(Path(file_path).resolve())))
