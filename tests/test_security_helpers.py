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

    def test_m3u_bundle_resolves_and_claims_child_discs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / 'sub'
            sub.mkdir()
            disc1 = root / 'Disc 1.chd'
            disc2 = sub / 'Disc 2.chd'
            disc1.write_bytes(b'MComprHD')
            disc2.write_bytes(b'MComprHD')
            playlist = root / 'Game.m3u'
            playlist.write_text('#EXTM3U\nDisc 1.chd\nsub/Disc 2.chd\n', encoding='utf-8')

            parsed = gamebooks._parse_m3u_bundle(str(playlist))
            self.assertEqual(parsed['missing_files'], [])
            self.assertEqual(set(map(os.path.realpath, parsed['resolved_files'])), {os.path.realpath(disc1), os.path.realpath(disc2)})

            bundle = gamebooks._collect_disk_bundle_paths(str(playlist))
            self.assertEqual(set(map(os.path.realpath, bundle)), {os.path.realpath(playlist), os.path.realpath(disc1), os.path.realpath(disc2)})

            found = {
                'playlist': {'filename': playlist.name, 'file_path': str(playlist)},
                'disc1': {'filename': disc1.name, 'file_path': str(disc1)},
                'disc2': {'filename': disc2.name, 'file_path': str(disc2)},
            }
            filtered, claimed = gamebooks._filter_m3u_claimed_files(found)
            self.assertEqual(set(filtered), {'playlist'})
            self.assertEqual(claimed, {os.path.realpath(disc1), os.path.realpath(disc2)})

    def test_m3u_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outside = root.parent / 'outside-test.chd'
            outside.write_bytes(b'MComprHD')
            try:
                playlist = root / 'Unsafe.m3u'
                playlist.write_text('../outside-test.chd\n', encoding='utf-8')
                parsed = gamebooks._parse_m3u_bundle(str(playlist))
                self.assertEqual(parsed['resolved_files'], [])
                self.assertIn('../outside-test.chd', parsed['invalid_references'])
            finally:
                outside.unlink(missing_ok=True)

    def test_move_m3u_bundle_moves_discs_and_rewrites_playlist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'ps1'
            sub = source / 'discs'
            target = root / 'psx'
            sub.mkdir(parents=True)
            disc1 = source / 'Disc 1.chd'
            disc2 = sub / 'Disc 2.chd'
            disc1.write_bytes(b'MComprHD')
            disc2.write_bytes(b'MComprHD')
            playlist = source / 'Game.m3u'
            playlist.write_text('# 멀티디스크\nDisc 1.chd\n"discs/Disc 2.chd"\n', encoding='utf-8')

            result = gamebooks._move_disk_bundle(str(playlist), str(target))
            self.assertTrue(result['moved'])
            moved_playlist = target / 'Game.m3u'
            self.assertTrue(moved_playlist.exists())
            self.assertTrue((target / 'Disc 1.chd').exists())
            self.assertTrue((target / 'Disc 2.chd').exists())
            text = moved_playlist.read_text(encoding='utf-8')
            self.assertIn('Disc 1.chd', text)
            self.assertIn('"Disc 2.chd"', text)
            self.assertNotIn('discs/Disc 2.chd', text)

    def test_mame_compatibility_health_marks_nonworking_game_unsupported(self):
        status, reason = gamebooks._mame_compatibility_health('astrass')
        self.assertEqual(status, 'unsupported')
        self.assertIn('mame2003=game not working', reason)
        self.assertIn('mame2003_plus=game not working', reason)

    def test_mame_compatibility_health_keeps_working_game_pass(self):
        self.assertEqual(gamebooks._mame_compatibility_health('bakubaku'), ('pass', ''))

    def test_emulatorjs_unsupported_reason_uses_analyzer_warning(self):
        reason = gamebooks._emulatorjs_unsupported_reason({
            'emulatorjs_supported': False,
            'analysis_warnings': ['MAME2003 계열 게임 호환성 제한: mame2003=game not working'],
        })
        self.assertIn('game not working', reason)
        self.assertEqual(gamebooks._emulatorjs_unsupported_reason({'emulatorjs_supported': True}), '')

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
