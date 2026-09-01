#!/usr/bin/env python3
"""Game Books 배포 ZIP 생성 및 manifest/SQLite 무결성 검증."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "bookoasis_gamebooks.py"
VERSION_FILE = ROOT / "VERSION"
SQLITE_FILES = {
    "arcade_dat.db",
    "libs/rom_database/data/arcade_dat.db",
    "libs/rom_database/data/mame_compatibility.db",
    "libs/rom_database/data/rom_metadata.db",
}


def read_update_manifest(source: Path = PLUGIN_SOURCE) -> dict:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "BookoasisGamebooksMetadataProvider":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == "update_manifest" for target in stmt.targets):
                value = ast.literal_eval(stmt.value)
                if not isinstance(value, dict):
                    raise ValueError("update_manifest가 dict가 아닙니다.")
                return value
    raise ValueError("BookoasisGamebooksMetadataProvider.update_manifest를 찾지 못했습니다.")


def read_version(version_file: Path = VERSION_FILE) -> str:
    payload = json.loads(version_file.read_text(encoding="utf-8"))
    version = str(payload.get("plugin version") or "").strip()
    if not version:
        raise ValueError("VERSION의 plugin version이 비어 있습니다.")
    return version


def validate_manifest_files(root: Path, manifest: dict) -> list[str]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("update_manifest.files가 비어 있습니다.")

    clean: list[str] = []
    seen: set[str] = set()
    for raw in files:
        rel = str(raw or "").strip().replace("\\", "/")
        posix = PurePosixPath(rel)
        if not rel or posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"안전하지 않은 manifest 경로: {raw!r}")
        if rel in seen:
            raise ValueError(f"중복 manifest 경로: {rel}")
        path = root / rel
        if not path.is_file():
            raise FileNotFoundError(f"manifest 파일 누락: {rel}")
        seen.add(rel)
        clean.append(rel)
    return clean


def validate_sqlite_files(root: Path, files: list[str]) -> None:
    missing = SQLITE_FILES.difference(files)
    if missing:
        raise ValueError(f"필수 SQLite DB가 manifest에서 빠졌습니다: {sorted(missing)}")

    for rel in sorted(SQLITE_FILES):
        path = root / rel
        with path.open("rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                raise ValueError(f"SQLite 헤더 오류: {rel}")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        if not result or result[0] != "ok":
            raise ValueError(f"SQLite integrity_check 실패: {rel}: {result}")


def build_release_zip(output: Path, root: Path = ROOT) -> tuple[Path, Path, list[str]]:
    manifest = read_update_manifest(root / "bookoasis_gamebooks.py")
    files = validate_manifest_files(root, manifest)
    validate_sqlite_files(root, files)

    version = read_version(root / "VERSION")
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = "bookoasis_gamebooks"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in files:
            zf.write(root / rel, f"{prefix}/{rel}")

    with zipfile.ZipFile(output, "r") as zf:
        bad_member = zf.testzip()
        if bad_member:
            raise ValueError(f"ZIP CRC 검증 실패: {bad_member}")
        archived = {
            name[len(prefix) + 1 :]
            for name in zf.namelist()
            if name.startswith(prefix + "/") and not name.endswith("/")
        }
    if archived != set(files):
        missing = sorted(set(files) - archived)
        extra = sorted(archived - set(files))
        raise ValueError(f"ZIP과 manifest 불일치: missing={missing}, extra={extra}")

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(f"Game Books {version} release ZIP: {output}")
    print(f"managed files: {len(files)}")
    print(f"sha256: {digest}")
    return output, checksum, files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_release_zip(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
