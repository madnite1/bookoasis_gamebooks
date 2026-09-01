import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIBS = ROOT / "libs"
if str(LIBS) not in sys.path:
    sys.path.insert(0, str(LIBS))

from PIL import Image  # noqa: E402
from library_structures import LibraryManager  # noqa: E402
from rom_analyzer.models import ArcadeInfo, DiscInfo, RomAnalysisResult  # noqa: E402


class LibraryStructuresIntegrationTests(unittest.TestCase):
    @staticmethod
    def _analysis(
        path: Path,
        *,
        system_id="snes",
        platform_slug="snes",
        system_name="Super Nintendo",
        is_disc=False,
        disc_info=None,
        is_playable=True,
        arcade_info=None,
    ):
        return RomAnalysisResult(
            file_path=str(path),
            file_name=path.name,
            file_size=path.stat().st_size,
            file_ext=path.suffix.lower(),
            system_id=system_id,
            system_name=system_name,
            system_type="arcade" if system_id in {"arcade", "neogeo"} else "console",
            platform_slug=platform_slug,
            is_disc=is_disc,
            is_playable=is_playable,
            disc_info=disc_info or DiscInfo(),
            arcade_info=arcade_info or ArcadeInfo(),
        )

    @staticmethod
    def _cover_bytes(width=1200, height=1800):
        output = io.BytesIO()
        Image.new("RGB", (width, height), (100, 120, 140)).save(output, format="PNG")
        return output.getvalue()

    def test_package_initializes_inside_requested_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "library-root"
            manager = LibraryManager(str(root))
            paths = manager.get_paths()

            self.assertEqual(Path(paths.root), root.resolve())
            self.assertEqual(Path(paths.library_dir), root / "library")
            self.assertEqual(Path(paths.resources_dir), root / "resources" / "roms")
            self.assertEqual(Path(paths.assets_dir), root / "assets" / "users")
            self.assertEqual(Path(paths.config_dir), root / "config")
            self.assertTrue(Path(paths.library_dir).is_dir())
            self.assertTrue(Path(paths.resources_dir).is_dir())
            self.assertTrue(Path(paths.assets_dir).is_dir())
            self.assertTrue(Path(paths.config_dir).is_dir())

    def test_single_rom_is_placed_under_stable_game_id_directory(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "upload" / "Chrono Trigger.sfc"
            source.parent.mkdir()
            source.write_bytes(b"rom-data")
            root = base / "library-root"
            manager = LibraryManager(str(root))

            result = manager.place_content(self._analysis(source), game_id=42, move_files=True)

            expected = root / "library" / "snes" / "roms" / "42" / source.name
            self.assertTrue(result.success)
            self.assertEqual(Path(result.rom_dest_path), expected)
            self.assertEqual(result.game_id, "42")
            self.assertTrue(expected.is_file())
            self.assertFalse(source.exists())

    def test_multidisc_bundle_uses_game_id_directory_and_preserves_relative_paths(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source_dir = base / "upload"
            discs_dir = source_dir / "Discs"
            discs_dir.mkdir(parents=True)
            playlist = source_dir / "Game.m3u"
            disc1 = discs_dir / "Game (Disc 1).chd"
            disc2 = discs_dir / "Game (Disc 2).chd"
            playlist.write_text("Discs/Game (Disc 1).chd\nDiscs/Game (Disc 2).chd\n", encoding="utf-8")
            disc1.write_bytes(b"disc-1")
            disc2.write_bytes(b"disc-2")
            disc_info = DiscInfo(
                is_disc=True,
                is_multi_file=True,
                is_complete=True,
                referenced_files=["Discs/Game (Disc 1).chd", "Discs/Game (Disc 2).chd"],
                disc_count=2,
            )
            analysis = self._analysis(
                playlist,
                system_id="psx",
                platform_slug="psx",
                system_name="Sony PlayStation",
                is_disc=True,
                disc_info=disc_info,
            )
            root = base / "library-root"
            manager = LibraryManager(str(root))

            result = manager.place_content(analysis, game_id=427, move_files=False)

            game_dir = root / "library" / "psx" / "roms" / "427"
            self.assertTrue(result.success)
            self.assertEqual(Path(result.rom_dest_path), game_dir / "Game.m3u")
            self.assertEqual(
                {Path(path) for path in result.companion_dest_paths},
                {
                    game_dir / "Discs" / "Game (Disc 1).chd",
                    game_dir / "Discs" / "Game (Disc 2).chd",
                },
            )
            self.assertTrue(playlist.is_file())
            self.assertTrue(disc1.is_file())
            self.assertTrue(disc2.is_file())

    def test_game_id_content_does_not_allow_rename_conflict_strategy(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "Game.sfc"
            source.write_bytes(b"rom")
            manager = LibraryManager(str(Path(td) / "library-root"))
            with self.assertRaises(ValueError):
                manager.place_content(self._analysis(source), game_id=9, conflict_strategy="rename")

    def test_replacing_content_keeps_same_game_id_directory(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            first = base / "first" / "Game.sfc"
            second = base / "second" / "Game.sfc"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"old")
            second.write_bytes(b"new")
            root = base / "library-root"
            manager = LibraryManager(str(root))

            first_result = manager.place_content(self._analysis(first), game_id=77, move_files=False)
            second_result = manager.place_content(self._analysis(second), game_id=77, move_files=False)

            expected = root / "library" / "snes" / "roms" / "77" / "Game.sfc"
            self.assertTrue(first_result.success)
            self.assertTrue(second_result.success)
            self.assertEqual(Path(first_result.rom_dest_path), expected)
            self.assertEqual(Path(second_result.rom_dest_path), expected)
            self.assertEqual(expected.read_bytes(), b"new")
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())

    def test_bios_is_placed_in_platform_bios_directory(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "neogeo.zip"
            source.write_bytes(b"bios")
            analysis = self._analysis(
                source,
                system_id="neogeo",
                platform_slug="neogeo",
                system_name="Neo-Geo BIOS",
                is_playable=False,
                arcade_info=ArcadeInfo(is_arcade=True, is_bios_set=True),
            )
            root = Path(td) / "library-root"
            manager = LibraryManager(str(root))

            result = manager.place_bios(analysis, move_files=False)

            expected = root / "library" / "neogeo" / "bios" / "neogeo.zip"
            self.assertTrue(result.success)
            self.assertEqual(Path(result.bios_dest_path), expected)
            self.assertEqual(expected.read_bytes(), b"bios")

    def test_cover_small_and_large_are_real_different_resolutions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "library-root"
            manager = LibraryManager(str(root))

            result = manager.save_cover(427, self._cover_bytes())

            self.assertTrue(result.success)
            small = Path(result.cover_s_dest_path)
            large = Path(result.cover_l_dest_path)
            self.assertEqual(small, root / "resources" / "roms" / "427" / "cover_s.webp")
            self.assertEqual(large, root / "resources" / "roms" / "427" / "cover_l.webp")
            with Image.open(small) as image:
                self.assertEqual(image.width, 320)
                self.assertEqual(image.height, 480)
            with Image.open(large) as image:
                self.assertEqual(image.width, 1024)
                self.assertEqual(image.height, 1536)

    def test_cover_conversion_does_not_upscale_small_original(self):
        with tempfile.TemporaryDirectory() as td:
            manager = LibraryManager(str(Path(td) / "library-root"))
            result = manager.save_cover(1, self._cover_bytes(200, 300))
            self.assertTrue(result.success)
            with Image.open(result.cover_s_dest_path) as small, Image.open(result.cover_l_dest_path) as large:
                self.assertEqual(small.size, (200, 300))
                self.assertEqual(large.size, (200, 300))

    def test_library_structures_does_not_download_cover_urls(self):
        with tempfile.TemporaryDirectory() as td:
            manager = LibraryManager(str(Path(td) / "library-root"))
            result = manager.save_cover(1, "https://example.com/cover.png")
            self.assertFalse(result.success)
            self.assertTrue(any("네트워크 URL" in error for error in result.errors))

    def test_user_save_and_state_names_do_not_depend_on_rom_filename(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "library-root"
            manager = LibraryManager(str(root))

            save_result = manager.save_user_save("user_7", 427, b"save-data")
            state_result = manager.save_user_state("user_7", 427, b"state-data", slot=2)

            self.assertTrue(save_result.success)
            self.assertTrue(state_result.success)
            self.assertEqual(
                Path(save_result.save_dest_path),
                root / "assets" / "users" / "user_7" / "427" / "saves" / "default.sav",
            )
            self.assertEqual(
                Path(state_result.state_dest_path),
                root / "assets" / "users" / "user_7" / "427" / "states" / "slot_2.state",
            )
            self.assertEqual(Path(save_result.save_dest_path).read_bytes(), b"save-data")
            self.assertEqual(Path(state_result.state_dest_path).read_bytes(), b"state-data")

    def test_resource_and_user_paths_cannot_escape_library_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "library-root"
            manager = LibraryManager(str(root))
            resource_dir = Path(manager.structure.get_resource_rom_dir("../../escape")).resolve()
            user_dirs = manager.structure.get_user_asset_dirs("../user", "../../game")

            self.assertTrue(resource_dir.is_relative_to((root / "resources" / "roms").resolve()))
            for path in user_dirs.values():
                self.assertTrue(Path(path).resolve().is_relative_to((root / "assets" / "users").resolve()))

    def test_plugin_requirements_do_not_shadow_bookoasis_core_pillow(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        package_names = {
            line.split(";", 1)[0].strip().split("==", 1)[0].split(">=", 1)[0].split("<=", 1)[0].strip().lower()
            for line in requirements
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertNotIn("pillow", package_names)


if __name__ == "__main__":
    unittest.main()
