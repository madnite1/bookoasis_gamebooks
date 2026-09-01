import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIBS = ROOT / "libs"
sys.path[:0] = [str(ROOT), str(LIBS)]

import bookoasis_gamebooks as gamebooks
import rom_analysis_adapter as adapter
import rom_analyzer
import rom_database


class RomAnalysisAdapterTests(unittest.TestCase):
    def test_vendored_analyzer_imports(self):
        self.assertTrue(adapter.is_analyzer_available())
        info = adapter.get_vendor_info()
        self.assertEqual(info.get("version"), rom_analyzer.__version__)
        self.assertTrue(info.get("git_commit"))
        self.assertIn("rom_analyzer", info.get("packages", []))
        self.assertIn("rom_database", info.get("packages", []))
        self.assertEqual(Path(rom_database.__file__).resolve().parent, (LIBS / "rom_database").resolve())
        db_paths = rom_database.DatabasePaths.default()
        self.assertTrue(db_paths.metadata.is_file())
        self.assertTrue(db_paths.dat.is_file())
        self.assertTrue(db_paths.compatibility.is_file())

    def test_result_conversion_keeps_gamebooks_contract(self):
        with tempfile.TemporaryDirectory() as td:
            m3u = Path(td) / "Game.m3u"
            disc = Path(td) / "Disc 1.chd"
            m3u.write_text("Disc 1.chd\n", encoding="utf-8")
            disc.write_bytes(b"x")
            result = SimpleNamespace(
                system_id="psx", is_arcade=False, is_playable=True,
                confidence_score=0.97, file_path=str(m3u), identity_status="strong",
                warnings=[], conflicts=[], detection_methods=["m3u_playlist"],
                arcade_info=SimpleNamespace(required_bios=[], parent_rom=None, chd_name=None, matched_count=0, total_roms=0, match_rate=0.0, driver=None),
                disc_info=SimpleNamespace(referenced_files=["Disc 1.chd"], missing_files=[], disc_count=1, track_count=1),
                bios_info=SimpleNamespace(bios_files=["scph5500.bin", "scph5501.bin"], mandatory=False, needs_bios=False),
                header_metadata=SimpleNamespace(title="Game", serial="SLUS-00001"),
                emulatorjs=SimpleNamespace(supported=True, core="pcsx_rearmed", system="psx", reason=None),
            )
            converted = adapter._convert_result(result)
            self.assertEqual(converted["core"], "psx")
            self.assertEqual(converted["platform"], "PS1")
            self.assertEqual(converted["needed_bios"], "scph5501.bin")
            self.assertFalse(converted["bios_mandatory"])
            self.assertFalse(converted["bios_needed"])
            self.assertEqual(converted["emulatorjs_reason"], "")
            self.assertEqual(converted["resolved_disk_files"], [str(disc)])
            self.assertEqual(converted["metadata_confidence"], 97)

    def test_analyze_rom_uses_one_raw_analysis_result(self):
        raw_result = object()
        analyze = mock.Mock(return_value=raw_result)
        converted = {"core": "n64", "platform": "N64", "title": "Modern"}
        with mock.patch.object(adapter, "_load_analyzer", return_value=(analyze, "test")), \
             mock.patch.object(adapter, "convert_result", return_value=converted) as convert:
            self.assertEqual(adapter.analyze_rom("dummy.zip"), converted)
            analyze.assert_called_once_with("dummy.zip", compute_hashes=False)
            convert.assert_called_once_with(raw_result)

    def test_analyze_rom_context_keeps_raw_modern_result(self):
        raw_result = object()
        modern = {"core": "n64", "platform": "N64", "title": "Modern"}
        with mock.patch.object(adapter, "analyze_result", return_value=raw_result) as analyze, \
             mock.patch.object(adapter, "convert_result", return_value=modern) as convert, \
             mock.patch.object(gamebooks, "_detect_rom_info_legacy") as legacy:
            result, info = gamebooks._analyze_rom_context("dummy.zip")
            self.assertIs(result, raw_result)
            self.assertEqual(info, modern)
            analyze.assert_called_once_with("dummy.zip")
            convert.assert_called_once_with(raw_result)
            legacy.assert_not_called()

    def test_detect_rom_info_returns_context_dict(self):
        raw_result = object()
        modern = {"core": "n64", "platform": "N64", "title": "Modern"}
        with mock.patch.object(gamebooks, "_analyze_rom_context", return_value=(raw_result, modern)) as context:
            self.assertEqual(gamebooks._detect_rom_info("dummy.zip"), modern)
            context.assert_called_once_with("dummy.zip")

    def test_analyze_rom_context_falls_back_when_modern_is_unknown(self):
        raw_result = object()
        legacy_result = {"core": "arcade", "platform": "Arcade", "title": "Legacy"}
        with mock.patch.object(adapter, "analyze_result", return_value=raw_result), \
             mock.patch.object(adapter, "convert_result", return_value={"core": "", "platform": ""}), \
             mock.patch.object(gamebooks, "_detect_rom_info_legacy", return_value=legacy_result) as legacy:
            result, info = gamebooks._analyze_rom_context("dummy.zip")
            self.assertIsNone(result)
            self.assertEqual(info, legacy_result)
            legacy.assert_called_once_with("dummy.zip")

    def test_analyze_rom_context_falls_back_when_modern_raises(self):
        legacy_result = {"core": "snes", "platform": "SNES", "title": "Legacy"}
        with mock.patch.object(adapter, "analyze_result", side_effect=RuntimeError("분석 실패")), \
             mock.patch.object(gamebooks, "_detect_rom_info_legacy", return_value=legacy_result) as legacy:
            result, info = gamebooks._analyze_rom_context("dummy.sfc")
            self.assertIsNone(result)
            self.assertEqual(info, legacy_result)
            legacy.assert_called_once_with("dummy.sfc")


if __name__ == "__main__":
    unittest.main()
