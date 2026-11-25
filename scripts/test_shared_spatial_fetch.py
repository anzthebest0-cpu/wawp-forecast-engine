import unittest
from shared_spatial_fetch import SharedFetch
URL="https://data.ecmwf.int/forecasts/20261125/00z/test.grib2"
class Tests(unittest.TestCase):
    def test_reuse_and_cap(self):
        calls=[]
        def source(url,cap,interval):calls.append((url,cap,interval));return b"abcd"
        f=SharedFetch(source,max_bytes=8)
        self.assertEqual(f(URL,4,(0,4)),f(URL,8,(0,4)))
        self.assertEqual(len(calls),1)
        with self.assertRaises(ValueError):f(URL,3,(0,4))
        f(URL,4,(4,4))
        with self.assertRaises(ValueError):f(URL,4,(8,4))
        with self.assertRaises(ValueError):f(URL.replace("20261125","20261124"),4,(0,4))
    def test_failure_not_cached(self):
        f=SharedFetch(lambda *args:b"bad")
        with self.assertRaises(ValueError):f(URL,4,(0,4))
        self.assertEqual(f.store,{})
if __name__=="__main__":unittest.main()
