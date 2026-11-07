import unittest
from classify_kvm_trace import classify

def event(address, code):
    return f'qemu-system-x86-123 [003] ..... 1.234567: kvm_page_fault: vcpu 1 rip 0x100 address {hex(address)} error_code {hex(code)}'

class TraceTests(unittest.TestCase):
    def test_half_open_range_and_overlapping_access(self):
        rows, summary = classify('\n'.join(event(a, 3) for a in (0xfff, 0x1000, 0x1fff, 0x2000)), 0x1000, 0x1000)
        self.assertEqual(summary['range']['candidate_CXL'], 2)
        self.assertEqual(summary['access_bit_counts'], {'READ':4, 'WRITE':4, 'EXEC':0})
        self.assertEqual(rows[0]['timestamp'], 1.234567)
    def test_reject_unparsed_events(self):
        with self.assertRaises(ValueError): classify('kvm_page_fault: unexpected', 0, 4096)
    def test_empty_capture(self):
        self.assertEqual(classify('cpus=128\n', 0, 4096)[1]['total'], 0)

if __name__ == '__main__': unittest.main()
