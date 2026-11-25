import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location("adapter", Path(__file__).resolve().parents[1] / "scripts" / "normalize_awosnet_xml.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
NOW=datetime(2026,11,25,4,tzinfo=timezone.utc)
XML=b'<awos><airportIdentifier value="WAWP"/><date value="25/11/2026"/><time value="03:59"/><twoMinutewindSpeed value="0" units="knots"/><twoMinuteWindDirection value="240" units="degrees"/><windGust value=""/></awos>'
class TestAdapter(unittest.TestCase):
    def test_values(self):
        r=a.normalize(XML,NOW,confirmed=True)
        self.assertEqual(r["wind_speed_kt"],0)
        self.assertEqual(r["wind_direction_deg"],240)
        self.assertIsNone(r["gust_kt"])
        self.assertIsNone(r["qnh_hpa"])
        self.assertEqual(r["observed_at_utc"],"2026-11-25T03:59:00Z")
    def test_reject(self):
        for x in [XML.replace(b"WAWP",b"WAAA"),XML.replace(b"03:59",b"04:01"),XML.replace(b"knots",b"m/s"),XML.replace(b'value="0"',b'value="5abc"'),XML.replace(b'value="0"',b'value="-1"'),XML.replace(b"</awos>",b'<windGust value="1"/></awos>'),b'<!DOCTYPE a [<!ENTITY x "a">]><a>&x;</a>']:
            with self.subTest(x=x):
                with self.assertRaises(ValueError): a.normalize(x,NOW,confirmed=True)
        with self.assertRaises(ValueError): a.normalize(XML,NOW)
