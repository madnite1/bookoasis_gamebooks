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



if __name__ == '__main__':
    unittest.main()
