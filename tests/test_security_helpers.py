import os
import tempfile
import unittest
import zipfile
from pathlib import Path

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
