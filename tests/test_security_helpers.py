import os
import tempfile
import unittest
import zipfile
from pathlib import Path
import bookoasis_gamebooks as gamebooks

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

    def test_cover_fallback_prefers_platform_and_non_romm_path(self):
        provider_cls = gamebooks.BookoasisGamebooksMetadataProvider
        provider = provider_cls.__new__(provider_cls)
        with tempfile.TemporaryDirectory() as td:
            Path(td, 'library_arcade_roms_avspirit.zip_11111111.png').write_bytes(b'a')
            expected = Path(td, 'roms_arcade_avspirit.zip_22222222.png')
            expected.write_bytes(b'b')
            Path(td, 'roms_snes_avspirit.zip_33333333.png').write_bytes(b'c')
            provider._get_covers_dir = lambda: td
            provider._db_execute = lambda *args, **kwargs: 1
            resolved = provider._resolve_existing_cover(
                'roms_mame2003_avspirit.zip_deadbeef',
                'avspirit.zip',
                'mame2003',
            )
            self.assertEqual(os.path.realpath(resolved), os.path.realpath(expected))

    def test_cover_fallback_supports_unicode_filename(self):
        provider_cls = gamebooks.BookoasisGamebooksMetadataProvider
        provider = provider_cls.__new__(provider_cls)
        with tempfile.TemporaryDirectory() as td:
            expected = Path(td, 'roms_gba_젤다의전설.zip_12345678.png')
            expected.write_bytes(b'x')
            provider._get_covers_dir = lambda: td
            provider._db_execute = lambda *args, **kwargs: 1
            resolved = provider._resolve_existing_cover(
                'roms_gba_newid_deadbeef',
                '젤다의전설.zip',
                'gba',
            )
            self.assertEqual(os.path.realpath(resolved), os.path.realpath(expected))



if __name__ == '__main__':
    unittest.main()
