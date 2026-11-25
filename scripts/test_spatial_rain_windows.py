import unittest,numpy as np
from spatial_rain_windows import interval
def rec(s,e,v,pe=.01):
    return dict(start=s,end=e,array=np.array([v],float),packing_error_mm=pe,step="accum",short="tp",units="m",dx=.25,level="surface",code=[0,1,8])
class Tests(unittest.TestCase):
    def test_native(self):self.assertEqual(interval([rec(21,24,3)],21,24)["array"][0],3)
    def test_difference(self):self.assertEqual(interval([rec(0,21,4),rec(0,24,7)],21,24)["array"][0],3)
    def test_tile(self):self.assertEqual(interval([rec(84,90,3),rec(90,96,4)],84,96)["array"][0],7)
    def test_roundoff(self):self.assertEqual(interval([rec(0,21,4),rec(0,24,3.99)],21,24)["array"][0],0)
    def test_bad_negative(self):self.assertIsNone(interval([rec(0,21,4),rec(0,24,3)],21,24))
    def test_gap(self):self.assertIsNone(interval([rec(84,90,3),rec(91,96,4)],84,96))
    def test_conflict(self):
        with self.assertRaises(ValueError):interval([rec(21,24,3),rec(21,24,4)],21,24)
if __name__=="__main__":unittest.main()
