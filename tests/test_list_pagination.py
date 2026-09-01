import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask
from werkzeug.exceptions import NotFound

import bookoasis_gamebooks as gamebooks


class ListPaginationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.roms = self.root / "roms"
        self.covers = self.root / "covers"
        self.bios = self.root / "bios"
        self.saves = self.root / "saves"
        for path in (self.data, self.roms, self.covers, self.bios, self.saves):
            path.mkdir(parents=True, exist_ok=True)

        provider_cls = gamebooks.BookoasisGamebooksMetadataProvider
        self.provider = provider_cls.__new__(provider_cls)
        self.provider._get_data_dir = lambda: str(self.data)
        self.provider._get_roms_dir = lambda: str(self.roms)
        self.provider._get_emulatorjs_root = lambda: str(self.root)
        self.provider._get_covers_dir = lambda: str(self.covers)
        self.provider._get_bios_dir = lambda: str(self.bios)
        self.provider._get_user_saves_dir = lambda _user_id: str(self.saves)
        self.provider._get_setting = lambda _key, default="": default
        self.provider._ensure_routes = lambda: None
        self.provider._init_db()

        rows = [
            ("g1", "alpha.gba", "Alpha", "gba", "GBA", "pass", "2026-01-01T00:00:00"),
            ("g2", "bravo.nes", "Bravo Puzzle", "nes", "NES", "pass", "2026-01-02T00:00:00"),
            ("g3", "charlie.zip", "Charlie", "arcade", "Arcade", "parent_required", "2026-01-03T00:00:00"),
            ("g4", "delta.zip", "Delta", "mame2003", "Arcade", "unsupported", "2026-01-04T00:00:00"),
            ("g5", "echo.sfc", "Echo", "snes", "SNES", "pass", "2026-01-05T00:00:00"),
        ]
        for game_id, filename, title, core, platform, health, added_at in rows:
            rom = self.roms / filename
            rom.write_bytes(b"rom")
            self.provider._db_execute(
                """INSERT INTO games
                   (id, filename, file_path, title, game_code, core, platform, size_bytes,
                    added_at, cover_path, health_status, missing_roms, metadata_source,
                    metadata_confidence, region_tag, revision_tag, disc_number, content_flags,
                    rom_md5, rom_sha1, description, alt_titles, developer, publisher)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '', 'test', 99, '', '', 0, '',
                           'heavy-md5', 'heavy-sha1', 'heavy-description', 'heavy-alt', 'heavy-dev', 'heavy-pub')""",
                (
                    game_id,
                    filename,
                    str(rom),
                    title,
                    game_id,
                    core,
                    platform,
                    len(b"rom"),
                    added_at,
                    health,
                ),
            )
        self.provider._db_execute(
            "INSERT INTO user_game_data (user_id, game_id, is_favorite, last_played_at, play_count) VALUES (1, 'g2', 1, '2026-01-06T00:00:00', 3)"
        )
        self.app = Flask(__name__)

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, query):
        with self.app.test_request_context("/?action=list_games&" + query), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1), \
             mock.patch.object(gamebooks, "_is_current_user_admin", return_value=True):
            return self.provider.get_dashboard_data("general")

    def test_server_pagination_has_stable_page_boundaries(self):
        first = self._call("offset=0&limit=2&sort=newest&category=all&status=all")
        second = self._call("offset=2&limit=2&sort=newest&category=all&status=all")

        self.assertEqual([g["id"] for g in first["games"]], ["g5", "g4"])
        self.assertEqual([g["id"] for g in second["games"]], ["g3", "g2"])
        self.assertEqual(first["total_count"], 5)
        self.assertEqual(first["library_total_count"], 5)
        self.assertEqual(first["next_offset"], 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(second["next_offset"], 4)
        self.assertTrue(second["has_more"])
        self.assertNotIn("config", first)
        self.assertNotIn("available_bios", first)
        self.assertNotIn("config", second)
        self.assertNotIn("available_bios", second)
        self.assertTrue(all(game["runtime_state_loaded"] is False for game in first["games"]))

    def test_server_filters_apply_before_pagination(self):
        arcade = self._call("offset=0&limit=40&sort=title&category=arcade&status=all")
        favorite = self._call("offset=0&limit=40&sort=newest&category=all&status=all&favorite_only=1")
        unverified = self._call("offset=0&limit=40&sort=newest&category=all&status=unverified")
        search = self._call("offset=0&limit=40&sort=newest&category=all&status=all&q=puzzle")

        self.assertEqual([g["id"] for g in arcade["games"]], ["g3", "g4"])
        self.assertEqual([g["id"] for g in favorite["games"]], ["g2"])
        self.assertEqual([g["id"] for g in unverified["games"]], ["g3"])
        self.assertEqual(unverified["games"][0]["health_status"], "unverified")
        self.assertEqual([g["id"] for g in search["games"]], ["g2"])

    def test_list_games_does_not_touch_runtime_filesystem_helpers(self):
        with mock.patch.object(self.provider, "_scan_roms", side_effect=AssertionError("scan must not run")), \
             mock.patch.object(self.provider, "_list_available_bios_names", side_effect=AssertionError("bios scan must not run")), \
             mock.patch.object(self.provider, "_get_user_saves_dir", side_effect=AssertionError("save dir must not be read")), \
             mock.patch.object(self.provider, "_get_bios_dir", side_effect=AssertionError("bios dir must not be read")), \
             mock.patch.object(self.provider, "_get_covers_dir", side_effect=AssertionError("cover dir must not be read")), \
             mock.patch.object(self.provider, "_get_emulatorjs_root", side_effect=AssertionError("runtime root must not be read")):
            data = self._call("offset=0&limit=2&sort=newest&category=all&status=all")

        self.assertTrue(data["success"])
        self.assertEqual([g["id"] for g in data["games"]], ["g5", "g4"])

    def test_runtime_state_returns_filesystem_state_separately(self):
        (self.saves / "g2.sav").write_bytes(b"save")
        (self.bios / "gba_bios.bin").write_bytes(b"bios")

        with self.app.test_request_context("/?action=runtime_state&game_ids=g2,g5&include_globals=1"), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1), \
             mock.patch.object(gamebooks, "_is_current_user_admin", return_value=True):
            data = self.provider.get_dashboard_data("general")

        self.assertTrue(data["success"])
        self.assertEqual(data["game_states"]["g2"]["has_save"], 1)
        self.assertEqual(data["game_states"]["g5"]["has_save"], 0)
        self.assertIn("gba_bios.bin", data["available_bios"])
        self.assertIn("config", data)

    def test_frontend_does_not_auto_query_runtime_state_after_list_render(self):
        script = (Path(__file__).resolve().parents[1] / "script.js").read_text()
        self.assertNotIn("queueRuntimeStateRefresh(state.games, true);", script)
        self.assertNotIn("queueRuntimeStateRefresh(incoming, false);", script)

    def test_cover_route_uses_db_path_without_recovery_scan(self):
        cover_path = self.covers / "g5.png"
        payload = b"\x89PNG\r\n\x1a\ncover"
        cover_path.write_bytes(payload)
        self.provider._db_execute(
            "UPDATE games SET cover_path = ? WHERE id = ?",
            (str(cover_path), "g5"),
        )

        with self.app.test_request_context("/"), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1), \
             mock.patch.object(
                 self.provider,
                 "_resolve_existing_cover",
                 side_effect=AssertionError("cover recovery must not run"),
             ):
            response = self.provider._route_cover_file("g5")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), payload)
        self.assertEqual(response.mimetype, "image/png")

    def test_cover_route_does_not_fallback_when_db_path_is_missing(self):
        missing_path = self.covers / "missing.png"
        fallback_path = self.covers / "g5.png"
        fallback_path.write_bytes(b"fallback")
        self.provider._db_execute(
            "UPDATE games SET cover_path = ? WHERE id = ?",
            (str(missing_path), "g5"),
        )

        with self.app.test_request_context("/"), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1), \
             mock.patch.object(
                 self.provider,
                 "_resolve_existing_cover",
                 side_effect=AssertionError("cover recovery must not run"),
             ):
            with self.assertRaises(NotFound):
                self.provider._route_cover_file("g5")

        row = self.provider._db_query("SELECT cover_path FROM games WHERE id = ?", ("g5",))[0]
        self.assertEqual(row["cover_path"], str(missing_path))

    def test_list_payload_omits_heavy_unused_fields(self):
        data = self._call("offset=0&limit=1&sort=newest&category=all&status=all")
        game = data["games"][0]
        for key in (
            "file_path", "rom_crc32", "rom_md5", "rom_sha1", "description",
            "alt_titles", "developer", "publisher", "canonical_title", "play_count",
        ):
            self.assertNotIn(key, game)
        for key in (
            "id", "filename", "title", "core", "platform", "health_status",
            "missing_roms", "rom_url", "cover_url", "save_url", "state_url",
            "relative_path", "has_save", "has_state", "is_favorite",
        ):
            self.assertIn(key, game)


if __name__ == "__main__":
    unittest.main()
