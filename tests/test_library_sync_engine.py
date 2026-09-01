import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bookoasis_gamebooks as gamebooks


class LibrarySyncEngineTests(unittest.TestCase):
    def _provider(self):
        return object.__new__(gamebooks.BookoasisGamebooksMetadataProvider)

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

            result = provider._scan_roms()

            self.assertEqual(result["new_count"], 0)
            self.assertEqual(result["deleted_count"], 1)
            self.assertTrue(any("DELETE FROM games" in sql for sql, _ in deleted))
            self.assertTrue(any("DELETE FROM user_game_data" in sql for sql, _ in deleted))

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

    def test_ingest_places_root_rom_in_detected_core_folder(self):
        provider = self._provider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            roms = root / "roms"
            bios = root / "bios"
            covers = root / "covers"
            roms.mkdir()
            bios.mkdir()
            covers.mkdir()
            source = roms / "test.nes"
            source.write_bytes(b"NES\x1a" + b"\x00" * 64)

            executed = []
            provider._migrate_bios_files = lambda: None
            provider._get_roms_dir = lambda: str(roms)
            provider._get_bios_dir = lambda: str(bios)
            provider._get_covers_dir = lambda: str(covers)
            provider._get_setting = lambda key, default="": default
            provider._db_query = lambda sql, params=(): []
            provider._db_execute = lambda sql, params=(): executed.append((sql, params))
            provider._resolve_existing_cover = lambda *args, **kwargs: None

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
            with mock.patch.object(gamebooks, "_detect_rom_info", return_value=analysis), \
                 mock.patch.object(gamebooks, "_collect_identity_fields", return_value=identity), \
                 mock.patch.object(gamebooks, "_derive_health_status_from_analysis", return_value=("pass", "")), \
                 mock.patch.object(gamebooks, "_resolve_korean_game_title", return_value="Test"):
                result = provider._run_library_sync("ingest")

            target = roms / "nes" / "test.nes"
            self.assertFalse(source.exists())
            self.assertTrue(target.is_file())
            self.assertEqual(result["new_count"], 1)
            self.assertTrue(any("INSERT OR REPLACE INTO games" in sql for sql, _ in executed))


if __name__ == "__main__":
    unittest.main()
