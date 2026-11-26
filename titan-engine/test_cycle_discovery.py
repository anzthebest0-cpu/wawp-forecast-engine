import unittest
from datetime import datetime,timezone
from pilot_cycle_discovery import LEADS,candidates,acceptable
class Tests(unittest.TestCase):
    def test_schedule(self):
        self.assertEqual(len(LEADS),17)
        self.assertEqual(LEADS,tuple(sorted(set(LEADS))))
        self.assertEqual((LEADS[0],LEADS[-1]),(0,96))
    def test_no_future_cycle(self):
        n=datetime(2026,11,25,1,tzinfo=timezone.utc)
        c=candidates(n)
        self.assertEqual(c[0].hour,0)
        self.assertEqual(c[1].day,24)
        self.assertTrue(all(x<=n for x in c))
    def test_status(self):
        self.assertFalse(acceptable({"status":"partial"}))
        self.assertTrue(acceptable({"status":"metadata_complete"}))
if __name__=="__main__":unittest.main()
