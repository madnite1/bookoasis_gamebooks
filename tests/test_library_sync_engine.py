import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bookoasis_gamebooks as gamebooks
from rom_analyzer.models import RomAnalysisResult


class LibrarySyncEngineTests(unittest.TestCase):
    def _provider(self):
        return object.__new__(gamebooks.BookoasisGamebooksMetadataProvider)

    def _provider_with_db(self, db_path):
        provider = self._provider()
        provider._get_db_path = lambda: str(db_path)
        return provider

    def test_modes_share_single_entrypoint(self):
        provider = self._provider()
        with mock.patch.object(provider, "_scan_roms", return_value={"success": True}) as scan, \
             mock.patch.object(provider, "_refresh_health_statuses") as health:
            provider._run_library_sync("ingest")
            scan.assert_called_once_with(new_only=True)
            scan.reset_mock()

            provider._run_library_sync("sync")
            scan.assert_called_once_with()
            scan.reset_mock()

            provider._run_library_sync("rebuild")
            scan.assert_called_once_with(force_full=True)
            scan.reset_mock()

            provider._run_library_sync("diagnose")
            health.assert_called_once_with()
            scan.assert_not_called()

    def test_invalid_mode_is_rejected(self):
        provider = self._provider()
        with self.assertRaises(ValueError):
            provider._run_library_sync("unknown")

    def test_sync_reconciles_deleted_games_even_without_changed_files(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            roms = root / "roms"
            bios = root / "bios"
            covers = root / "covers"
            roms.mkdir()
            bios.mkdir()
            covers.mkdir()

            deleted = []
            provider._migrate_bios_files = lambda: None
            provider._get_roms_dir = lambda: str(roms)
            provider._get_bios_dir = lambda: str(bios)
            provider._get_covers_dir = lambda: str(covers)
            provider._get_setting = lambda key, default="": default
            provider._db_query = lambda sql, params=(): [
                {
                    "id": "roms_missing_zip",
                    "filename": "missing.zip",
                    "file_path": str(roms / "missing.zip"),
                    "size_bytes": 123,
                    "mtime": 1.0,
                    "cover_path": "",
                    "core": "arcade",
                    "platform": "Arcade",
                }
            ] if "SELECT * FROM games" in sql else []
            provider._db_execute = lambda sql, params=(): deleted.append((sql, params))
            deleted_future_ids = []
            provider._delete_future_game_id = lambda game_id: deleted_future_ids.append(game_id) or True

            result = provider._scan_roms()

            self.assertEqual(result["new_count"], 0)
            self.assertEqual(result["deleted_count"], 1)
            self.assertTrue(any("DELETE FROM games" in sql for sql, _ in deleted))
            self.assertTrue(any("DELETE FROM user_game_data" in sql for sql, _ in deleted))
            self.assertEqual(deleted_future_ids, ["roms_missing_zip"])

    def test_future_ids_backfill_once_and_survive_reinit(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "gba.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE games (
                    id TEXT PRIMARY KEY,
                    filename TEXT,
                    file_path TEXT,
                    title TEXT,
                    game_code TEXT,
                    maker_code TEXT,
                    core TEXT,
                    platform TEXT,
                    size_bytes INTEGER,
                    mtime REAL,
                    added_at TEXT,
                    cover_path TEXT,
                    needed_bios TEXT
                )"""
            )
            conn.execute("INSERT INTO games (id, filename) VALUES (?, ?)", ("legacy_a", "a.zip"))
            conn.execute("INSERT INTO games (id, filename) VALUES (?, ?)", ("legacy_b", "b.zip"))
            conn.commit()
            conn.close()

            provider = self._provider_with_db(db_path)
            provider._init_db()
            first_rows = provider._db_query("SELECT id, future_id FROM games ORDER BY id")
            first_map = provider._db_query("SELECT future_id, legacy_id FROM game_id_map ORDER BY future_id")

            provider._init_db()
            second_rows = provider._db_query("SELECT id, future_id FROM games ORDER BY id")
            second_map = provider._db_query("SELECT future_id, legacy_id FROM game_id_map ORDER BY future_id")

            self.assertEqual(first_rows, second_rows)
            self.assertEqual(first_map, second_map)
            self.assertEqual({row["legacy_id"] for row in first_map}, {"legacy_a", "legacy_b"})
            self.assertTrue(all(int(row["future_id"] or 0) > 0 for row in first_rows))

    def test_future_id_rebind_keeps_same_integer(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "gba.db"
            provider = self._provider_with_db(db_path)
            provider._init_db()
            provider._db_execute("INSERT INTO games (id) VALUES (?)", ("old_game",))
            future_id = provider._get_or_create_future_game_id("old_game")
            provider._db_execute(
                "INSERT INTO games (id, future_id) VALUES (?, ?)",
                ("new_game", future_id),
            )

            rebound = provider._rebind_future_game_id("old_game", "new_game", future_id)

            self.assertEqual(rebound, future_id)
            mapped = provider._db_query(
                "SELECT future_id, legacy_id FROM game_id_map WHERE future_id = ?",
                (future_id,),
            )
            self.assertEqual(mapped, [{"future_id": future_id, "legacy_id": "new_game"}])
            self.assertEqual(
                provider._db_query("SELECT future_id FROM games WHERE id = ?", ("new_game",))[0]["future_id"],
                future_id,
            )
            self.assertEqual(
                provider._db_query("SELECT future_id FROM game_id_map WHERE legacy_id = ?", ("old_game",)),
                [],
            )

    def test_deleted_future_id_is_not_reused(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "gba.db"
            provider = self._provider_with_db(db_path)
            provider._init_db()
            provider._db_execute("INSERT INTO games (id) VALUES (?)", ("old_game",))
            old_future_id = provider._get_or_create_future_game_id("old_game")
            provider._db_execute("DELETE FROM games WHERE id = ?", ("old_game",))
            self.assertTrue(provider._delete_future_game_id("old_game"))

            provider._db_execute("INSERT INTO games (id) VALUES (?)", ("new_game",))
            new_future_id = provider._get_or_create_future_game_id("new_game")

            self.assertGreater(new_future_id, old_future_id)

    def test_user_state_and_save_follow_game_id_change(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            saves = Path(td) / "user_7"
            saves.mkdir()
            (saves / "old_game.sav").write_bytes(b"save")
            (saves / "old_game_slot1.state").write_bytes(b"state")

            executed = []

            def query(sql, params=()):
                if "WHERE game_id = ?" in sql and "user_id = ?" not in sql:
                    return [{"user_id": 7, "is_favorite": 1, "last_played_at": "2026-08-31 10:00:00", "play_count": 5}]
                if "WHERE user_id = ? AND game_id = ?" in sql:
                    return []
                return []

            provider._db_query = query
            provider._db_execute = lambda sql, params=(): executed.append((sql, params))
            provider._get_user_saves_dir = lambda user_id=None: str(saves)

            self.assertTrue(provider._merge_game_user_state("old_game", "new_game"))
            self.assertTrue(any("SET game_id = ?" in sql and params[0] == "new_game" for sql, params in executed))
            self.assertFalse((saves / "old_game.sav").exists())
            self.assertTrue((saves / "new_game.sav").is_file())
            self.assertFalse((saves / "old_game_slot1.state").exists())
            self.assertTrue((saves / "new_game_slot1.state").is_file())

    def test_relocation_identity_rejects_hash_mismatch(self):
        provider = self._provider()
        self.assertTrue(provider._relocation_identity_matches(
            {"rom_sha1": "AABB"},
            {"rom_sha1": "aabb"},
        ))
        self.assertFalse(provider._relocation_identity_matches(
            {"rom_sha1": "AABB"},
            {"rom_sha1": "CCDD"},
        ))
        self.assertTrue(provider._relocation_identity_matches(
            {"rom_sha1": ""},
            {"rom_sha1": ""},
        ))

    def test_library_sync_rejects_overlapping_operation(self):
        provider = self._provider()
        acquired = gamebooks._LIBRARY_SYNC_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            result = provider._run_library_sync("sync")
        finally:
            gamebooks._LIBRARY_SYNC_LOCK.release()
        self.assertFalse(result["success"])
        self.assertTrue(result["busy"])

    def test_newer_destination_save_wins_and_old_file_is_removed(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            saves = Path(td) / "user_7"
            saves.mkdir()
            old_state = saves / "old_game.state"
            new_state = saves / "new_game.state"
            old_state.write_bytes(b"old")
            new_state.write_bytes(b"new")
            os.utime(old_state, (1, 1))
            os.utime(new_state, (2, 2))

            def query(sql, params=()):
                if "WHERE game_id = ?" in sql and "user_id = ?" not in sql:
                    return [{"user_id": 7, "is_favorite": 0, "last_played_at": "", "play_count": 0}]
                if "WHERE user_id = ? AND game_id = ?" in sql:
                    return []
                return []

            provider._db_query = query
            provider._db_execute = lambda sql, params=(): None
            provider._get_user_saves_dir = lambda user_id=None: str(saves)

            self.assertTrue(provider._merge_game_user_state("old_game", "new_game"))
            self.assertFalse(old_state.exists())
            self.assertEqual(new_state.read_bytes(), b"new")

    def test_health_diagnose_reuses_cache_for_unchanged_rom(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "gba.db"
            bios = root / "bios"
            bios.mkdir()
            rom = root / "game.nes"
            rom.write_bytes(b"NES\x1a" + b"x" * 128)

            provider = self._provider_with_db(db_path)
            provider._get_bios_dir = lambda: str(bios)
            provider._init_db()
            provider._db_execute(
                "INSERT INTO games (id, filename, file_path, core, platform, health_status) VALUES (?, ?, ?, ?, ?, ?)",
                ("game", rom.name, str(rom), "nes", "NES", "pass"),
            )
            analysis = {
                "metadata_source": "rom-analyzer",
                "metadata_confidence": 100,
                "source_system": "header",
            }

            with mock.patch("rom_analysis_adapter.analyze_rom", return_value=analysis) as analyze, \
                 mock.patch.object(gamebooks, "_derive_health_status_from_analysis", return_value=("pass", "")):
                provider._refresh_health_statuses()
                analyze.assert_called_once_with(str(rom))

            first = provider._db_query(
                "SELECT health_cache_key, health_status FROM games WHERE id = ?", ("game",)
            )[0]
            self.assertTrue(first["health_cache_key"])
            self.assertEqual(first["health_status"], "pass")

            with mock.patch("rom_analysis_adapter.analyze_rom") as analyze_again:
                provider._refresh_health_statuses()
                analyze_again.assert_not_called()
            self.assertEqual(gamebooks._HEALTH_PROGRESS["cached"], 1)
            self.assertEqual(gamebooks._HEALTH_PROGRESS["failed"], 0)

    def test_health_diagnose_reports_path_mismatch_without_updating_db_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "gba.db"
            bios = root / "bios"
            bios.mkdir()
            actual = root / "actual.nes"
            actual.write_bytes(b"rom")
            stale = root / "old" / "actual.nes"

            provider = self._provider_with_db(db_path)
            provider._get_bios_dir = lambda: str(bios)
            provider._init_db()
            provider._db_execute(
                "INSERT INTO games (id, filename, file_path, core, platform, health_status) VALUES (?, ?, ?, ?, ?, ?)",
                ("game", actual.name, str(stale), "nes", "NES", "pass"),
            )
            resolver = mock.Mock(return_value=str(actual))
            provider._resolve_existing_rom_path = resolver

            with mock.patch("rom_analysis_adapter.analyze_rom") as analyze:
                provider._refresh_health_statuses()
                analyze.assert_not_called()

            self.assertFalse(resolver.call_args.kwargs["update_db"])
            row = provider._db_query(
                "SELECT file_path, health_status, missing_roms FROM games WHERE id = ?", ("game",)
            )[0]
            self.assertEqual(row["file_path"], str(stale))
            self.assertEqual(row["health_status"], "path_mismatch")
            self.assertIn("라이브러리 동기화", row["missing_roms"])

    def test_health_diagnose_distinguishes_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "gba.db"
            bios = root / "bios"
            bios.mkdir()
            missing = root / "missing.nes"

            provider = self._provider_with_db(db_path)
            provider._get_bios_dir = lambda: str(bios)
            provider._init_db()
            provider._db_execute(
                "INSERT INTO games (id, filename, file_path, core, platform, health_status) VALUES (?, ?, ?, ?, ?, ?)",
                ("game", missing.name, str(missing), "nes", "NES", "unverified"),
            )
            provider._resolve_existing_rom_path = mock.Mock(return_value=None)

            with mock.patch("rom_analysis_adapter.analyze_rom") as analyze:
                provider._refresh_health_statuses()
                analyze.assert_not_called()

            row = provider._db_query(
                "SELECT health_status, missing_roms FROM games WHERE id = ?", ("game",)
            )[0]
            self.assertEqual(row["health_status"], "missing_file")
            self.assertIn("찾을 수 없습니다", row["missing_roms"])

    def test_health_diagnose_transient_analyzer_failure_keeps_previous_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "gba.db"
            bios = root / "bios"
            bios.mkdir()
            rom = root / "game.nes"
            rom.write_bytes(b"rom")

            provider = self._provider_with_db(db_path)
            provider._get_bios_dir = lambda: str(bios)
            provider._init_db()
            provider._db_execute(
                "INSERT INTO games (id, filename, file_path, core, platform, health_status, missing_roms, metadata_source, metadata_confidence, source_system) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("game", rom.name, str(rom), "nes", "NES", "pass", "기존 판정", "rom-analyzer", 90, "header"),
            )

            with mock.patch("rom_analysis_adapter.analyze_rom", side_effect=RuntimeError("temporary")):
                provider._refresh_health_statuses()

            row = provider._db_query(
                "SELECT health_status, missing_roms, metadata_confidence, health_cache_key FROM games WHERE id = ?",
                ("game",),
            )[0]
            self.assertEqual(row["health_status"], "pass")
            self.assertEqual(row["missing_roms"], "기존 판정")
            self.assertEqual(row["metadata_confidence"], 90)
            self.assertEqual(row["health_cache_key"], "")
            self.assertEqual(gamebooks._HEALTH_PROGRESS["failed"], 1)

    def test_health_bundle_cache_invalidates_when_sidecar_set_changes(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cue = root / "disc.cue"
            bin_file = root / "disc.bin"
            cue.write_text('FILE "disc.bin" BINARY\n', encoding="utf-8")
            bin_file.write_bytes(b"disc")
            game = {"id": "disc", "core": "psx", "platform": "PS1", "file_path": str(cue)}

            state1 = provider._health_bundle_cache_state(game, str(cue), state_cache={})
            key1 = provider._health_cache_key(
                game, "engine", "ok", str(cue), os.stat(cue), bundle_state=state1
            )

            bin_file.unlink()
            now = os.stat(root).st_mtime_ns + 1_000_000_000
            os.utime(root, ns=(now, now))
            state2 = provider._health_bundle_cache_state(game, str(cue), state_cache={})
            key2 = provider._health_cache_key(
                game, "engine", "ok", str(cue), os.stat(cue), bundle_state=state2
            )

            self.assertNotEqual(state1, state2)
            self.assertNotEqual(key1, key2)

    def test_health_bios_cache_reuses_same_required_bios_check(self):
        provider = self._provider()
        cache = {}
        game1 = {"needed_bios": "neogeo.zip", "health_status": "pass"}
        game2 = {"needed_bios": "neogeo.zip", "health_status": "bios_required"}
        with mock.patch.object(gamebooks, "_is_required_bios_available", return_value=True) as check:
            first = provider._health_bios_cache_state(game1, {"neogeo.zip"}, "/bios", state_cache=cache)
            second = provider._health_bios_cache_state(game2, {"neogeo.zip"}, "/bios", state_cache=cache)
        self.assertEqual(first, "neogeo.zip:1")
        self.assertEqual(second, first)
        check.assert_called_once()

    def test_health_diagnose_cache_hit_does_not_write_database(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bios = root / "bios"
            bios.mkdir()
            rom = root / "game.nes"
            rom.write_bytes(b"rom")
            provider._get_bios_dir = lambda: str(bios)
            provider._health_engine_signature = lambda _bios: "engine"
            game = {
                "id": "game", "filename": rom.name, "file_path": str(rom),
                "core": "nes", "platform": "NES", "game_code": "",
                "health_status": "pass", "missing_roms": "",
                "metadata_source": "rom-analyzer", "metadata_confidence": 100,
                "source_system": "header",
            }
            game["health_cache_key"] = provider._health_cache_key(
                game, "engine", "ok", str(rom), os.stat(rom)
            )
            provider._db_query = mock.Mock(return_value=[game])
            provider._get_db_conn = mock.Mock(side_effect=AssertionError("cache hit must not write"))

            with mock.patch("rom_analysis_adapter.analyze_rom") as analyze:
                provider._refresh_health_statuses()
                analyze.assert_not_called()
            provider._get_db_conn.assert_not_called()
            self.assertEqual(gamebooks._HEALTH_PROGRESS["cached"], 1)

    def test_background_diagnose_does_not_leave_queued_on_lock_contention(self):
        provider = self._provider()
        original = dict(gamebooks._HEALTH_PROGRESS)
        acquired = gamebooks._LIBRARY_SYNC_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            gamebooks._HEALTH_PROGRESS.update({
                "is_running": False,
                "status": "queued",
                "failed": 0,
                "current_file": "",
            })
            result = provider._run_library_sync_background("diagnose")
            self.assertFalse(result["success"])
            self.assertTrue(result["busy"])
            self.assertEqual(gamebooks._HEALTH_PROGRESS["status"], "error")
            self.assertFalse(gamebooks._HEALTH_PROGRESS["is_running"])
        finally:
            gamebooks._LIBRARY_SYNC_LOCK.release()
            gamebooks._HEALTH_PROGRESS.clear()
            gamebooks._HEALTH_PROGRESS.update(original)

    def test_frontend_uses_unified_library_sync_api(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "script.js").read_text(encoding="utf-8")
        index = (root / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("apiCall('scan_new_roms'", script)
        self.assertNotIn("apiCall('full_scan'", script)
        self.assertNotIn("apiCall('health_refresh'", script)
        self.assertNotIn("apiCall('check_game'", script)
        self.assertEqual(script.count("apiCall('library_sync'"), 4)
        self.assertIn('id="gbaScanBtn" class="gba-btn gba-btn-secondary gba-admin-only"', index)
        self.assertIn("라이브러리 전체 재구축", index)
        self.assertIn("분석 결과 저장 ${saveCurrent} / ${saveTotal}", script)
        self.assertNotIn("percent = 95;", script)

    def test_db_execute_batches_uses_one_transaction_for_multiple_groups(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "batch.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
            conn.executemany("INSERT INTO items (id, value) VALUES (?, ?)", [(1, "a"), (2, "b")])
            conn.commit()
            conn.close()

            provider = self._provider_with_db(db_path)
            original_get_conn = provider._get_db_conn
            with mock.patch.object(provider, "_get_db_conn", wraps=original_get_conn) as get_conn:
                affected = provider._db_execute_batches([
                    ("UPDATE items SET value = ? WHERE id = ?", [("x", 1), ("y", 2)]),
                    ("UPDATE items SET value = value WHERE id = ?", [(1,), (2,)]),
                ])

            self.assertEqual(get_conn.call_count, 1)
            self.assertGreaterEqual(affected, 2)
            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT id, value FROM items ORDER BY id").fetchall()
            conn.close()
            self.assertEqual(rows, [(1, "x"), (2, "y")])

    def test_ingest_places_new_rom_in_structured_game_id_directory(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            roms = root / "roms"
            bios = root / "bios"
            covers = root / "covers"
            emulator_root = root / "emulatorjs"
            roms.mkdir()
            bios.mkdir()
            covers.mkdir()
            emulator_root.mkdir()
            source = roms / "test.nes"
            source.write_bytes(b"NES\x1a" + b"\x00" * 64)

            executed = []
            provider._migrate_bios_files = lambda: None
            provider._get_roms_dir = lambda: str(roms)
            provider._get_bios_dir = lambda: str(bios)
            provider._get_covers_dir = lambda: str(covers)
            provider._get_emulatorjs_root = lambda: str(emulator_root)
            provider._get_setting = lambda key, default="": default
            provider._db_query = lambda sql, params=(): []
            provider._db_execute = lambda sql, params=(): executed.append((sql, params))
            provider._resolve_existing_cover = lambda *args, **kwargs: None
            future_ids = []
            provider._get_or_create_future_game_id = lambda legacy_id: future_ids.append(legacy_id) or 101

            raw_analysis = RomAnalysisResult(
                file_path=str(source),
                file_name=source.name,
                file_size=source.stat().st_size,
                file_ext=".nes",
                system_id="nes",
                system_name="Nintendo Entertainment System",
                system_type="console",
                platform_slug="nes",
            )
            analysis = {
                "core": "nes", "platform": "NES", "title": "Test", "game_code": "",
                "maker_code": "", "needed_bios": "", "metadata_source": "rom-analyzer",
                "metadata_confidence": 100, "source_system": "header", "disk_missing_files": [],
            }
            identity = {
                "rom_crc32": "", "rom_md5": "", "rom_sha1": "", "serial_code": "",
                "normalized_title": "Test", "source_system": "header", "metadata_source": "rom-analyzer",
                "metadata_confidence": 100, "region_tag": "", "revision_tag": "", "disc_number": 0,
                "content_flags": "",
            }
            with mock.patch.object(gamebooks, "_analyze_rom_context", return_value=(raw_analysis, analysis)) as analyze_context, \
                 mock.patch.object(gamebooks, "_collect_identity_fields", return_value=identity), \
                 mock.patch.object(gamebooks, "_derive_health_status_from_analysis", return_value=("pass", "")), \
                 mock.patch.object(gamebooks, "_resolve_korean_game_title", return_value="Test"), \
                 mock.patch.object(gamebooks, "_enqueue_cover_downloads"):
                result = provider._run_library_sync("ingest")

            analyze_context.assert_called_once_with(str(source))
            intermediate = roms / "nes" / "test.nes"
            target = emulator_root / "library" / "nes" / "roms" / "101" / "test.nes"
            self.assertFalse(source.exists())
            self.assertFalse(intermediate.exists())
            self.assertTrue(target.is_file())
            self.assertEqual(result["new_count"], 1)
            final_gid = gamebooks._sanitize_id(f"{roms.name}_{os.path.relpath(intermediate, roms)}")
            self.assertEqual(future_ids, [final_gid])
            insert_calls = [(sql, params) for sql, params in executed if "INSERT OR REPLACE INTO games" in sql]
            self.assertEqual(len(insert_calls), 1)
            self.assertIn("(id, future_id,", insert_calls[0][0])
            self.assertEqual(insert_calls[0][1][1], 101)
            layout_updates = [(sql, params) for sql, params in executed if "SET layout_version = 2" in sql]
            self.assertEqual(len(layout_updates), 1)
            self.assertEqual(layout_updates[0][1][1], str(target))
            self.assertEqual(layout_updates[0][1][-1], final_gid)

    def test_ingest_keeps_legacy_layout_when_raw_analysis_is_unavailable(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            roms = root / "roms"
            bios = root / "bios"
            covers = root / "covers"
            emulator_root = root / "emulatorjs"
            roms.mkdir()
            bios.mkdir()
            covers.mkdir()
            emulator_root.mkdir()
            source = roms / "legacy.nes"
            source.write_bytes(b"NES\x1a" + b"\x00" * 64)

            executed = []
            provider._migrate_bios_files = lambda: None
            provider._get_roms_dir = lambda: str(roms)
            provider._get_bios_dir = lambda: str(bios)
            provider._get_covers_dir = lambda: str(covers)
            provider._get_emulatorjs_root = lambda: str(emulator_root)
            provider._get_setting = lambda key, default="": default
            provider._db_query = lambda sql, params=(): []
            provider._db_execute = lambda sql, params=(): executed.append((sql, params))
            provider._resolve_existing_cover = lambda *args, **kwargs: None
            provider._get_or_create_future_game_id = lambda legacy_id: 202

            analysis = {
                "core": "nes", "platform": "NES", "title": "Legacy", "game_code": "",
                "maker_code": "", "needed_bios": "", "metadata_source": "legacy",
                "metadata_confidence": 0, "source_system": "filename", "disk_missing_files": [],
            }
            identity = {
                "rom_crc32": "", "rom_md5": "", "rom_sha1": "", "serial_code": "",
                "normalized_title": "Legacy", "source_system": "filename", "metadata_source": "",
                "metadata_confidence": 0, "region_tag": "", "revision_tag": "", "disc_number": 0,
                "content_flags": "",
            }
            with mock.patch.object(gamebooks, "_analyze_rom_context", return_value=(None, analysis)), \
                 mock.patch.object(gamebooks, "_collect_identity_fields", return_value=identity), \
                 mock.patch.object(gamebooks, "_derive_health_status_from_analysis", return_value=("unverified", "")), \
                 mock.patch.object(gamebooks, "_resolve_korean_game_title", return_value="Legacy"), \
                 mock.patch.object(gamebooks, "_enqueue_cover_downloads"):
                result = provider._run_library_sync("ingest")

            target = roms / "nes" / "legacy.nes"
            self.assertFalse(source.exists())
            self.assertTrue(target.is_file())
            self.assertEqual(result["new_count"], 1)
            self.assertFalse(any("SET layout_version = 2" in sql for sql, _ in executed))
            self.assertFalse((emulator_root / "library").exists())

    def test_sync_keeps_layout_v2_game_when_structured_file_exists(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            roms = root / "roms"
            bios = root / "bios"
            covers = root / "covers"
            emulator_root = root / "emulatorjs"
            structured = emulator_root / "library" / "nes" / "roms" / "101" / "test.nes"
            roms.mkdir()
            bios.mkdir()
            covers.mkdir()
            structured.parent.mkdir(parents=True)
            structured.write_bytes(b"NES\x1a" + b"\x00" * 64)

            deleted = []
            provider._migrate_bios_files = lambda: None
            provider._get_roms_dir = lambda: str(roms)
            provider._get_bios_dir = lambda: str(bios)
            provider._get_covers_dir = lambda: str(covers)
            provider._get_emulatorjs_root = lambda: str(emulator_root)
            provider._get_setting = lambda key, default="": default
            provider._db_query = lambda sql, params=(): [{
                "id": "legacy_structured",
                "future_id": 101,
                "layout_version": 2,
                "filename": "test.nes",
                "file_path": str(structured),
                "size_bytes": structured.stat().st_size,
                "mtime": structured.stat().st_mtime,
                "cover_path": "",
                "core": "nes",
                "platform": "NES",
            }] if "SELECT * FROM games" in sql else []
            provider._db_execute = lambda sql, params=(): deleted.append((sql, params))
            provider._delete_future_game_id = lambda game_id: True

            result = provider._scan_roms()

            self.assertEqual(result["deleted_count"], 0)
            self.assertFalse(any("DELETE FROM games" in sql for sql, _ in deleted))


if __name__ == "__main__":
    unittest.main()
