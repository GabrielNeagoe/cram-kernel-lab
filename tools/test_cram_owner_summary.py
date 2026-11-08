import unittest

from summarize_cram_owners import window


class OwnershipWindowTest(unittest.TestCase):
    def test_reassignment_requires_a_later_active_observation(self):
        def scan(t, kind, page_list, state, available):
            return [t, t+.002, 8191, [[789, kind, page_list, state]], available]

        scans = [
            scan(1, 0, 6, "process-backed", []),
            scan(2, 0, 0, "zeroed", [789]),
            scan(3, 1, 2, "different-file", []),
            scan(4, 1, 6, "different-file", []),
            scan(5, 1, 6, "different-file", []),
        ]
        self.assertEqual(window(scans, 1.5, 3.5, 8192, {789})["ordered_reuse"], [])
        self.assertEqual(window(scans, 1.5, 4.5, 8192, {789})["ordered_reuse"], [
            dict(index=789, locked=True, free_or_zeroed_at_s=.502,
                 later_active_at_s=2.5, later_backing="different-file")
        ])


if __name__ == "__main__":
    unittest.main()
