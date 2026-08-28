import os
import tempfile
import unittest
import zipfile
import sys
import types
from pathlib import Path
from unittest import mock

import bookoasis_gamebooks as gamebooks
from tools import romm_migration_apply as migration


class SecurityHelperTests(unittest.TestCase):
    def test_path_containment(self):
        self.assertTrue(gamebooks._path_within('/tmp/gamebooks/rom', '/tmp/gamebooks'))
        self.assertTrue(gamebooks._path_within('/tmp/gamebooks', '/tmp/gamebooks'))
        self.assertFalse(gamebooks._path_within('/tmp/gamebooks2/rom', '/tmp/gamebooks'))
        self.assertFalse(gamebooks._path_within('/etc/passwd', '/tmp/gamebooks'))

    def test_zip_validation(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / 'good.zip'
            bad = Path(td) / 'bad.zip'
            with zipfile.ZipFile(good, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('rom.gba', b'ROMDATA')
            bad.write_bytes(b'not-a-zip')
            self.assertTrue(gamebooks._validate_zip_file(good))
            self.assertFalse(gamebooks._validate_zip_file(bad))

    def test_rollback_rejects_unexpected_root(self):
        result = migration.rollback_migration(target_romm_root=Path('/tmp/romm_library'))
        self.assertFalse(result['success'])
        self.assertEqual(result['deleted_files'], 0)
        self.assertTrue(any('Refusing rollback' in e for e in result['errors']))

    def test_remote_rollback_skips_fuse_walk_after_purge(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / 'data'
            data_dir.mkdir()
            db_path = data_dir / 'gba.db'
            db_path.write_bytes(b'current-db')
            backup = data_dir / 'gba.db.bak_migration_1'
            backup.write_bytes(b'backup-db')

            drive_helper = types.ModuleType('utils.drive_helper')
            drive_helper.get_rclone_relative_path = lambda _p: 'emulatorjs/romm_library'
            rclone_copy = types.ModuleType('utils.rclone_gdrive_copy')
            calls = []
            def fake_run(args, timeout=0):
                calls.append((args, timeout))
                return 0, b'', b''
            rclone_copy._run_rclone = fake_run

            with mock.patch.object(migration, '_is_expected_romm_root', return_value=True), \
                 mock.patch.object(migration.os, 'walk', side_effect=AssertionError('FUSE walk must be skipped')), \
                 mock.patch.dict(sys.modules, {
                     'utils.drive_helper': drive_helper,
                     'utils.rclone_gdrive_copy': rclone_copy,
                 }):
                result = migration.rollback_migration(
                    plugin_data_dir=str(data_dir),
                    target_romm_root=Path(td) / 'romm_library',
                    rclone_remote='google_drive',
                )

            self.assertTrue(result['success'])
            self.assertTrue(result['remote_purged'])
            self.assertEqual(result['cleanup_mode'], 'rclone')
            self.assertEqual(db_path.read_bytes(), b'backup-db')
            self.assertEqual(len(calls), 1)
            self.assertIn('--drive-use-trash=false', calls[0][0])

    def test_remote_rollback_does_not_restore_db_when_purge_fails(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / 'data'
            data_dir.mkdir()
            db_path = data_dir / 'gba.db'
            db_path.write_bytes(b'current-db')
            backup = data_dir / 'gba.db.bak_migration_1'
            backup.write_bytes(b'backup-db')

            drive_helper = types.ModuleType('utils.drive_helper')
            drive_helper.get_rclone_relative_path = lambda _p: 'emulatorjs/romm_library'
            rclone_copy = types.ModuleType('utils.rclone_gdrive_copy')
            rclone_copy._run_rclone = lambda _args, timeout=0: (1, b'', b'purge failed')

            with mock.patch.object(migration, '_is_expected_romm_root', return_value=True), \
                 mock.patch.object(migration.os, 'walk', side_effect=AssertionError('FUSE fallback must not run')), \
                 mock.patch.dict(sys.modules, {
                     'utils.drive_helper': drive_helper,
                     'utils.rclone_gdrive_copy': rclone_copy,
                 }):
                result = migration.rollback_migration(
                    plugin_data_dir=str(data_dir),
                    target_romm_root=Path(td) / 'romm_library',
                    rclone_remote='google_drive',
                )

            self.assertFalse(result['success'])
            self.assertFalse(result['db_restored'])
            self.assertEqual(result['cleanup_mode'], 'rclone_failed')
            self.assertEqual(db_path.read_bytes(), b'current-db')
            self.assertTrue(any('rclone purge failed' in e for e in result['errors']))

    def test_migration_copy_rejects_outside_paths(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'outside.rom'
            src.write_bytes(b'x')
            dst = migration.TARGET_ROMM_ROOT / 'library' / 'gba' / 'roms' / 'outside.rom'
            copied, failed = migration._batch_copy_files([(src, dst)])
            self.assertEqual(copied, 0)
            self.assertTrue(failed)
            self.assertIn('outside managed root', failed[0]['error'])


if __name__ == '__main__':
    unittest.main()
