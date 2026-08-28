from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

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

try:
    from bookoasis_gamebooks import (
        _collect_disk_bundle_paths,
        _resolve_disk_sidecars,
        _rewrite_disk_manifest_to_local_paths,
    )
except ImportError:
    from plugins.metadata.bookoasis_gamebooks.bookoasis_gamebooks import (
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
