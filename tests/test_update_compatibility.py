import ast
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_198_FILES = [
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
]
CRITICAL_RUNTIME_FILES = {
    "rom_analysis_adapter.py",
    "libs/rom_analyzer/analyzer.py",
    "libs/rom_database/manager.py",
    "libs/rom_database/data/arcade_dat.db",
    "libs/rom_database/data/mame_compatibility.db",
    "libs/rom_database/data/rom_metadata.db",
}


def _manifest_from_source(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "BookoasisGamebooksMetadataProvider":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "update_manifest"
                    for target in stmt.targets
                ):
                    return ast.literal_eval(stmt.value)
    raise AssertionError("update_manifest not found")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UpdateCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = _manifest_from_source(ROOT / "bookoasis_gamebooks.py")
        cls.files = cls.manifest["files"]

    def test_core_sample_update_button_is_disabled(self):
        self.assertFalse(self.manifest["show_sample_update_button"])

    def test_full_manifest_contains_legacy_and_new_runtime(self):
        self.assertTrue(set(LEGACY_198_FILES).issubset(self.files))
        self.assertTrue(CRITICAL_RUNTIME_FILES.issubset(self.files))
        self.assertIn("THIRD_PARTY_NOTICES.md", self.files)
        self.assertEqual(len(self.files), len(set(self.files)))
        missing = [rel for rel in self.files if not (ROOT / rel).is_file()]
        self.assertEqual(missing, [])

    def test_sqlite_binaries_in_manifest_are_valid(self):
        for rel in (
            "arcade_dat.db",
            "libs/rom_database/data/arcade_dat.db",
            "libs/rom_database/data/mame_compatibility.db",
            "libs/rom_database/data/rom_metadata.db",
        ):
            path = ROOT / rel
            with path.open("rb") as fh:
                self.assertEqual(fh.read(16), b"SQLite format 3\x00", rel)
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok", rel)
            finally:
                con.close()

    def test_release_zip_contains_exact_full_manifest(self):
        spec = importlib.util.spec_from_file_location(
            "build_release_zip", ROOT / "tools" / "build_release_zip.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "bookoasis_gamebooks-test.zip"
            module.build_release_zip(output, ROOT)
            with zipfile.ZipFile(output) as zf:
                names = {
                    name.removeprefix("bookoasis_gamebooks/")
                    for name in zf.namelist()
                    if name.startswith("bookoasis_gamebooks/") and not name.endswith("/")
                }
            self.assertEqual(names, set(self.files))

    def test_legacy_198_layout_can_be_replaced_by_latest_manifest_without_losing_unmanaged_data(self):
        """1.9.8의 10개 관리 파일에서 최신 full manifest로 올라가는 패키지 계약을 고정한다."""
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            installed = temp / "plugins" / "bookoasis_gamebooks"
            persistent = temp / "data" / "bookoasis_gamebooks"
            installed.mkdir(parents=True)
            persistent.mkdir(parents=True)

            for rel in LEGACY_198_FILES:
                target = installed / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"legacy-1.9.8")
            (installed / "runtime_marker.txt").write_text("keep-runtime", encoding="utf-8")
            (persistent / "user_marker.txt").write_text("keep-user-data", encoding="utf-8")

            # 최신 Plugin Manager ZIP 경로와 동일하게 최신 manifest 파일을 바이너리 복사한다.
            for rel in self.files:
                src = ROOT / rel
                dst = installed / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            version = json.loads((installed / "VERSION").read_text(encoding="utf-8"))["plugin version"]
            self.assertEqual(version, "1.9.20")
            self.assertEqual((installed / "runtime_marker.txt").read_text(), "keep-runtime")
            self.assertEqual((persistent / "user_marker.txt").read_text(), "keep-user-data")
            for rel in self.files:
                self.assertEqual(_sha256(installed / rel), _sha256(ROOT / rel), rel)


if __name__ == "__main__":
    unittest.main()
