import sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]; LIBS=ROOT/'libs'
sys.path[:0]=[str(ROOT),str(LIBS)]
import rom_analysis_adapter as adapter
class RomAnalysisAdapterTests(unittest.TestCase):
    def test_vendored_analyzer_imports(self):
        self.assertTrue(adapter.is_analyzer_available())
        info=adapter.get_vendor_info(); self.assertEqual(info.get('version'),'1.1.0'); self.assertTrue(info.get('git_commit'))
    def test_result_conversion_keeps_gamebooks_contract(self):
        with tempfile.TemporaryDirectory() as td:
            m3u=Path(td)/'Game.m3u'; disc=Path(td)/'Disc 1.chd'; m3u.write_text('Disc 1.chd\n',encoding='utf-8'); disc.write_bytes(b'x')
            r=SimpleNamespace(system_id='psx',is_arcade=False,is_playable=True,confidence_score=.97,file_path=str(m3u),identity_status='strong',warnings=[],conflicts=[],detection_methods=['m3u_playlist'],arcade_info=SimpleNamespace(required_bios=[],parent_rom=None,chd_name=None,matched_count=0,total_roms=0,match_rate=0.0,driver=None),disc_info=SimpleNamespace(referenced_files=['Disc 1.chd'],missing_files=[],disc_count=1,track_count=1),bios_info=SimpleNamespace(bios_files=['scph5501.bin']),header_metadata=SimpleNamespace(title='Game',serial='SLUS-00001'),emulatorjs=SimpleNamespace(supported=True,core='pcsx_rearmed',system='psx'))
            c=adapter._convert_result(r); self.assertEqual(c['core'],'psx'); self.assertEqual(c['platform'],'PS1'); self.assertEqual(c['needed_bios'],'scph5501.bin'); self.assertEqual(c['resolved_disk_files'],[str(disc)]); self.assertEqual(c['metadata_confidence'],97)
if __name__=='__main__': unittest.main()
