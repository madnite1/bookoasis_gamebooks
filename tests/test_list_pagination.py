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

    def _launch_plan(self, game_id):
        with self.app.test_request_context(f"/?action=launch_plan&game_id={game_id}"), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1), \
             mock.patch.object(gamebooks, "_is_current_user_admin", return_value=True):
            return self.provider.get_dashboard_data("general")

    def test_launch_plan_describes_direct_rom_and_saved_state(self):
        (self.saves / "g1.state").write_bytes(b"state-data")
        plan = self._launch_plan("g1")

        self.assertTrue(plan["success"])
        self.assertTrue(plan["launchable"])
        self.assertEqual(plan["delivery_mode"], "direct")
        self.assertFalse(plan["browser_unpack"])
        self.assertEqual(plan["rom_source_size"], len(b"rom"))
        self.assertEqual(plan["has_state"], 1)
        self.assertEqual(plan["state_url"], "/api/webhook/bookoasis_gamebooks/state/g1")
        self.assertTrue(plan["rom_url"].endswith("/rom/g1/alpha.gba"))

    def test_launch_plan_distinguishes_zip_7z_and_disk_bundle(self):
        archive_7z = self.roms / "converted.7z"
        archive_7z.write_bytes(b"7z-placeholder")
        cue = self.roms / "disc.cue"
        bin_file = self.roms / "disc.bin"
        bin_file.write_bytes(b"disc-binary")
        cue.write_text('FILE "disc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', encoding="utf-8")

        self.provider._db_execute(
            """INSERT INTO games (id, filename, file_path, title, game_code, core, platform, size_bytes, added_at, health_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pass')""",
            ("g6", archive_7z.name, str(archive_7z), "Converted", "g6", "psx", "PS1", archive_7z.stat().st_size, "2026-01-06T00:00:00"),
        )
        self.provider._db_execute(
            """INSERT INTO games (id, filename, file_path, title, game_code, core, platform, size_bytes, added_at, health_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pass')""",
            ("g7", cue.name, str(cue), "Disc Bundle", "g7", "psx", "PS1", cue.stat().st_size, "2026-01-07T00:00:00"),
        )

        zip_plan = self._launch_plan("g3")
        converted_plan = self._launch_plan("g6")
        bundle_plan = self._launch_plan("g7")

        self.assertEqual(zip_plan["delivery_mode"], "zip")
        self.assertTrue(zip_plan["browser_unpack"])
        self.assertEqual(converted_plan["delivery_mode"], "convert_7z")
        self.assertTrue(converted_plan["browser_unpack"])
        self.assertTrue(converted_plan["rom_url"].endswith("/converted.zip"))
        self.assertEqual(bundle_plan["delivery_mode"], "bundle_zip")
        self.assertTrue(bundle_plan["browser_unpack"])
        self.assertGreaterEqual(bundle_plan["bundle_file_count"], 2)
        self.assertGreater(bundle_plan["rom_source_size"], cue.stat().st_size)

    def test_launch_plan_selects_available_ps1_bios_without_global_bios_scan(self):
        ps1_rom = self.roms / "ps1game.bin"
        ps1_rom.write_bytes(b"ps1-rom")
        bios = self.bios / "scph5501.bin"
        bios.write_bytes(b"bios-data")
        self.provider._db_execute(
            """INSERT INTO games (id, filename, file_path, title, game_code, core, platform, size_bytes, added_at, health_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pass')""",
            ("g8", ps1_rom.name, str(ps1_rom), "PS1 Game", "g8", "psx", "PS1", ps1_rom.stat().st_size, "2026-01-08T00:00:00"),
        )

        with mock.patch.object(self.provider, "_list_available_bios_names", side_effect=AssertionError("global BIOS scan must not run")):
            plan = self._launch_plan("g8")

        self.assertTrue(plan["bios_available"])
        self.assertEqual(plan["bios_name"].lower(), "scph5501.bin")
        self.assertEqual(plan["bios_size"], len(b"bios-data"))
        self.assertIn("/bios/scph5501.bin?game_id=g8", plan["bios_url"])

    def test_launch_plan_blocks_missing_rom_before_emulator_boot(self):
        (self.roms / "alpha.gba").unlink()
        plan = self._launch_plan("g1")
        self.assertTrue(plan["success"])
        self.assertFalse(plan["launchable"])
        self.assertIn("ROM 파일", plan["blocked_reason"])

    def test_frontend_launch_progress_tracks_real_pipeline(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "script.js").read_text(encoding="utf-8")
        index = (root / "index.html").read_text(encoding="utf-8")

        self.assertIn("apiCall('launch_plan'", script)
        self.assertIn("게임 실행 준비 중...", script)
        self.assertIn("BIOS 파일 준비 중...", script)
        self.assertIn("BIOS 전송 중...", script)
        self.assertIn("디스크 번들 준비 중...", script)
        self.assertIn("실행 데이터 변환 중...", script)
        self.assertIn("게임 데이터 전송 중...", script)
        self.assertIn("저장 상태 복원 중...", script)
        self.assertIn("응답이 평소보다 오래 걸리고 있습니다.", script)
        self.assertIn("EmulatorJS 로더를 불러오지 못했습니다.", script)
        self.assertNotIn("0 B 다운로드", script)
        self.assertNotIn("바이오스 다운로드 중...", script)
        self.assertNotIn("게임 ZIP 다운로드 중...", script)
        self.assertNotIn("await apiCall('record_play'", script)
        self.assertIn('id="gbaLaunchActions"', index)
        self.assertIn('id="gbaLaunchRetryBtn"', index)
        self.assertIn('id="gbaLaunchCloseBtn"', index)

    def _analysis_detail(self, game_id, admin=False):
        with self.app.test_request_context(f"/?action=analysis_detail&game_id={game_id}"), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1), \
             mock.patch.object(gamebooks, "_is_current_user_admin", return_value=admin):
            return self.provider.get_dashboard_data("general")

    def _play_action(self, action, game_id, status=""):
        suffix = f"&status={status}" if status else ""
        with self.app.test_request_context(f"/?action={action}&game_id={game_id}{suffix}"), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1), \
             mock.patch.object(gamebooks, "_is_current_user_admin", return_value=False):
            return self.provider.get_dashboard_data("general")

    def test_analysis_detail_reuses_cached_snapshot_without_changing_health(self):
        self.provider._db_execute(
            "UPDATE games SET health_cache_key = 'health-a', health_status = 'pass', missing_roms = 'static-health' WHERE id = 'g1'"
        )
        fake = {
            "core": "gba",
            "platform": "GBA",
            "title": "Alpha",
            "game_code": "AGB-ALPHA",
            "metadata_source": "rom-analyzer",
            "metadata_confidence": 96,
            "source_system": "header",
            "analysis_methods": ["header", "hash"],
            "analysis_warnings": ["sample warning"],
            "analysis_conflicts": [],
            "resolved_disk_files": ["/private/server/disc1.bin"],
            "disk_missing_files": ["/private/server/disc2.bin"],
            "emulatorjs_supported": True,
            "emulatorjs_core": "gba",
            "emulatorjs_system": "Game Boy Advance",
            "emulatorjs_reason": "supported",
            "is_playable": True,
        }
        with mock.patch("rom_analysis_adapter.analyze_rom", return_value=fake) as analyze:
            first = self._analysis_detail("g1", admin=False)
            second = self._analysis_detail("g1", admin=False)

        self.assertEqual(analyze.call_count, 1)
        self.assertTrue(first["success"])
        self.assertFalse(first["analysis_cache_reused"])
        self.assertTrue(second["analysis_cache_reused"])
        self.assertEqual(second["analysis"]["resolved_disk_files"], ["disc1.bin"])
        self.assertEqual(second["analysis"]["disk_missing_files"], ["disc2.bin"])
        self.assertEqual(second["file"]["server_path"], "")
        row = self.provider._db_query(
            "SELECT health_status, missing_roms, health_cache_key, analysis_json, analysis_cache_key FROM games WHERE id='g1'"
        )[0]
        self.assertEqual(row["health_status"], "pass")
        self.assertEqual(row["missing_roms"], "static-health")
        self.assertEqual(row["health_cache_key"], "health-a")
        self.assertTrue(row["analysis_json"])
        self.assertTrue(row["analysis_cache_key"])

    def test_analysis_detail_admin_can_see_server_path(self):
        fake = {"core": "gba", "platform": "GBA", "metadata_source": "rom-analyzer"}
        with mock.patch("rom_analysis_adapter.analyze_rom", return_value=fake):
            detail = self._analysis_detail("g1", admin=True)
        self.assertEqual(detail["file"]["server_path"], str(self.roms / "alpha.gba"))

    def test_boot_and_manual_play_status_are_separate_from_health(self):
        self.provider._db_execute("UPDATE games SET health_cache_key='health-a' WHERE id='g1'")
        boot = self._play_action("record_boot", "g1")
        self.assertEqual(boot["play"]["status"], "booted")

        verified = self._play_action("set_play_status", "g1", "verified")
        self.assertEqual(verified["play"]["status"], "verified")

        boot_again = self._play_action("record_boot", "g1")
        self.assertEqual(boot_again["play"]["status"], "verified")
        row = self.provider._db_query("SELECT health_status, play_status FROM games WHERE id='g1'")[0]
        self.assertEqual(row["health_status"], "pass")
        self.assertEqual(row["play_status"], "verified")

    def test_play_verification_survives_health_cache_path_change(self):
        self.provider._db_execute(
            "UPDATE games SET health_cache_key='health-a', content_identity_key='content-a' WHERE id='g1'"
        )
        self._play_action("set_play_status", "g1", "verified")
        before = self._call("offset=0&limit=10&sort=title&category=all&status=all")
        before_game = next(game for game in before["games"] if game["id"] == "g1")
        self.assertEqual(before_game["play_status"], "verified")
        self.assertEqual(before_game["play_status_stale"], 0)

        # Phase 6처럼 경로/mtime 기반 health key만 바뀌어도 ROM 내용 identity는 그대로다.
        self.provider._db_execute("UPDATE games SET health_cache_key='health-b' WHERE id='g1'")
        after = self._call("offset=0&limit=10&sort=title&category=all&status=all")
        after_game = next(game for game in after["games"] if game["id"] == "g1")
        self.assertEqual(after_game["play_status"], "verified")
        self.assertEqual(after_game["play_status_stale"], 0)

    def test_play_verification_expires_when_content_identity_changes(self):
        self.provider._db_execute(
            "UPDATE games SET health_cache_key='health-a', content_identity_key='content-a' WHERE id='g1'"
        )
        self._play_action("set_play_status", "g1", "verified")
        self.provider._db_execute(
            "UPDATE games SET health_cache_key='health-b', content_identity_key='content-b' WHERE id='g1'"
        )

        after = self._call("offset=0&limit=10&sort=title&category=all&status=all")
        after_game = next(game for game in after["games"] if game["id"] == "g1")
        self.assertEqual(after_game["play_status"], "untested")
        self.assertEqual(after_game["play_status_stale"], 1)

    def test_content_identity_ignores_path_and_mtime_but_tracks_content_and_core(self):
        base = {
            "filename": "alpha.gba", "file_path": "/old/alpha.gba", "mtime": 1.0,
            "core": "gba", "platform": "GBA", "size_bytes": 123,
            "rom_sha1": "AABBCC", "rom_md5": "", "rom_crc32": "",
            "game_code": "AGB-ALPHA", "serial_code": "", "normalized_title": "alpha",
        }
        moved = dict(base, file_path="/new/library/gba/roms/1/alpha.gba", mtime=999.0)
        replaced = dict(base, rom_sha1="DDEEFF")
        other_core = dict(base, core="mgba-next")

        self.assertEqual(self.provider._content_identity_key(base), self.provider._content_identity_key(moved))
        self.assertNotEqual(self.provider._content_identity_key(base), self.provider._content_identity_key(replaced))
        self.assertNotEqual(self.provider._content_identity_key(base), self.provider._content_identity_key(other_core))

    def test_frontend_analysis_card_and_core_theme_contract(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "script.js").read_text(encoding="utf-8")
        index = (root / "index.html").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")
        card_start = script.index("function createGameCard(game)")
        card_end = script.index("  // 시스템 바이오스", card_start)
        card_source = script[card_start:card_end]

        self.assertIn('data-action="analysis-detail"', card_source)
        self.assertNotIn('출처:', card_source)
        self.assertIn("apiCall('analysis_detail'", script)
        self.assertIn("apiCall('record_boot'", script)
        self.assertIn("apiCall('set_play_status'", script)
        self.assertIn('id="gbaAnalysisModal"', index)
        self.assertIn("--gba-bg-main: var(--app-bg-main", style)
        self.assertIn("--gba-bg-card: var(--app-bg-card", style)
        self.assertIn("--gba-text-main: var(--app-text-primary", style)
        self.assertIn("--gba-primary: var(--app-accent", style)

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

    def test_cover_variants_use_future_id_and_list_prefers_small_url(self):
        future_id = self.provider._get_or_create_future_game_id("g5")
        source = self.covers / "g5.png"
        source.write_bytes(b"original-cover")
        self.provider._db_execute("UPDATE games SET cover_path = ? WHERE id = ?", (str(source), "g5"))

        large = self.root / "resources" / "roms" / str(future_id) / "cover_l.webp"
        small = self.root / "resources" / "roms" / str(future_id) / "cover_s.webp"
        large.parent.mkdir(parents=True, exist_ok=True)
        large.write_bytes(b"large-webp")
        small.write_bytes(b"small-webp")
        fake_result = mock.Mock(
            success=True,
            cover_l_dest_path=str(large),
            cover_s_dest_path=str(small),
            errors=[],
        )
        fake_manager = mock.Mock()
        fake_manager.save_cover.return_value = fake_result
        with mock.patch.object(self.provider, "_get_library_manager", return_value=fake_manager):
            result = self.provider._ensure_cover_variants("g5", force=True)

        self.assertTrue(result["success"])
        fake_manager.save_cover.assert_called_once_with(future_id, str(source))
        row = self.provider._db_query(
            "SELECT cover_large_path, cover_thumbnail_path, cover_revision FROM games WHERE id='g5'"
        )[0]
        self.assertEqual(row["cover_large_path"], str(large))
        self.assertEqual(row["cover_thumbnail_path"], str(small))
        self.assertEqual(int(row["cover_revision"]), 1)

        with self.app.test_request_context("/?size=small"), \
             mock.patch.object(gamebooks, "_get_current_user_id", return_value=1):
            response = self.provider._route_cover_file("g5")
        self.assertEqual(response.get_data(), b"small-webp")
        self.assertEqual(response.mimetype, "image/webp")

        data = self._call("offset=0&limit=10&sort=title&category=all&status=all")
        game = next(item for item in data["games"] if item["id"] == "g5")
        self.assertIn("size=small", game["cover_url"])
        self.assertIn("rev=1", game["cover_url"])
        self.assertNotIn("cover_thumbnail_path", game)
        self.assertNotIn("cover_large_path", game)

    def test_phase6_preflight_and_webp_frontend_contract(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "script.js").read_text(encoding="utf-8")
        index = (root / "index.html").read_text(encoding="utf-8")
        style = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="gbaPhase6PreflightBtn"', index)
        self.assertIn('id="gbaPhase6PreflightModal"', index)
        self.assertIn('id="gbaPhase6RepairBtn"', index)
        self.assertIn('id="gbaPhase6BackupBtn"', index)
        self.assertIn('id="gbaCoverWebpBtn"', index)
        self.assertIn("Phase 6 실제 ROM 마이그레이션은 시작하지 않습니다.", index)
        self.assertIn("const action = repair ? 'phase6_repair' : 'phase6_preflight'", script)
        self.assertIn("apiCall(action)", script)
        self.assertIn("apiCall('phase6_backup')", script)
        self.assertIn("apiCall('cover_webp_refresh')", script)
        self.assertIn("apiCall('cover_webp_progress')", script)
        self.assertIn(".gba-phase6-summary", style)
        self.assertIn("var(--gba-bg-card)", style)
        self.assertIn("var(--gba-border)", style)

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
