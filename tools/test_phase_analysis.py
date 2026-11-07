import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class PhaseAnalysisTests(unittest.TestCase):
    def run_case(self, loss=0, end='1-direct.first_read'):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name); source = root/'source'; source.mkdir(); out=root/'out'
        (source/'manifest.json').write_text(json.dumps(dict(dry_run=False, qemu_tids=[123], phases=['1-direct.first_read'])))
        (source/'trace-stats.json').write_text(json.dumps({'cpu0':f'overrun: {loss}\ncommit overrun: 0\ndropped events: 0\n'}))
        (source/'serial.txt').write_text('REGION_START\n0x490000000\nREGION_SIZE\n0x10000000\nMEASURE,1-direct.first_read,1.2,0,0\n')
        (source/'trace.txt').write_text(
            'python3-1 [000] ...1. 10.000000: tracing_mark_write: CRAM_BEGIN 1-direct.first_read\n'
            'qemu-system-x86-123 [000] ..... 10.001000: kvm_page_fault: vcpu 0 rip 0x100 address 0x490000000 error_code 0x181\n'
            f'python3-1 [000] ...1. 10.002000: tracing_mark_write: CRAM_END {end}\n'
            'qemu-system-x86-123 [000] ..... 10.003000: kvm_page_fault: vcpu 0 rip 0x100 address 0x100 error_code 0x184\n')
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('analyze_phases.py')),str(source),'--output-dir',str(out)],capture_output=True,text=True)
        return result,out

    def test_phase_and_between_phase_attribution(self):
        result,out=self.run_case(); self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads((out/'analysis.json').read_text())
        self.assertEqual(data['between_phase_events'],1)
        self.assertEqual(data['phases'][0]['read'],1)
        self.assertEqual(data['phases'][0]['cxl_events'],1)
        self.assertEqual(data['phases'][0]['bracket_ms'],2)

    def test_loss_rejected(self):
        result,out=self.run_case(loss=1)
        self.assertEqual(result.returncode,0)
        self.assertTrue((out/'analysis-rejected.txt').exists())
        self.assertFalse((out/'analysis.json').exists())

    def test_mismatched_marker_rejected(self):
        result,_=self.run_case(end='wrong')
        self.assertNotEqual(result.returncode,0)

if __name__ == '__main__': unittest.main()
