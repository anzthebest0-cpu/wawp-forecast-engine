import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location("adapter", Path(__file__).resolve().parents[1] / "scripts" / "normalize_awosnet_xml.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
# Synthetic values based on observed tag schema, not a captured station response.
NOW=datetime(2026,1,2,4,tzinfo=timezone.utc)
XML=b'''<awosnet>
<airportIdentifier value="WAWP"/><airportID value="WAWP"/>
<date value="02/01/2026" time="1767355200"/><time value="03:59:00" time="1767355200"/>
<twoMinutewindSpeed value="0" units="knots" time="1767355200"/><twoMinuteWindSpeed value="0" units="knots" time="1767355200"/>
<twoMinuteWindDirection value="240" units="degrees"/><twoMinutewindDirection value="240" units="degrees"/><windGust value="" units="knots"/>
<airTemperature value="30" units="deg C"/><dewPoint value="24" units="deg C"/>
<tenMinuteairTemperature value="29" units="deg C"/><tenMinuteDewPoint value="23" units="deg C"/><tenMinutedewPoint value="23" units="deg C"/>
<altimeterSetting value="1012.3" units="hPa"/><relativeHumidity value="70" units="percent"/>
<rainfallCurrentHour value="0" units="mm"/><solarRadiation value="400" units="W/m^2"/><densityAltitude value="2000" units="feet"/>
<METAR value="METAR WAWP 020350Z 24005KT CAVOK 30/24 Q1012 NOSIG="/>
</awosnet>'''
class TestAdapter(unittest.TestCase):
    def test_values(self):
        r=a.normalize(XML,NOW,confirmed=True)
        self.assertEqual(r["wind_speed_kt"],0)
        self.assertEqual(r["wind_direction_deg"],240)
        self.assertIsNone(r["gust_kt"])
        self.assertIsNone(r["qnh_hpa"])
        self.assertEqual(r["altimeter_setting_hpa"],1012.3)
        self.assertEqual(r["temperature_c"],30)
        self.assertEqual(r["temperature_10min_c"],29)
        self.assertEqual(r["dewpoint_10min_c"],23)
        self.assertEqual(r["relative_humidity_pct"],70)
        self.assertEqual(r["rain_current_hour_mm"],0)
        self.assertEqual(r["solar_wm2"],400)
        self.assertEqual(r["density_altitude_ft"],2000)
        self.assertEqual(r["observed_at_utc"],"2026-01-02T03:59:00Z")
        self.assertEqual(r["metar_date_utc"],"2026-01-02")
        self.assertEqual(r["source_epoch_offset_seconds"],1767355200-int(datetime(2026,1,2,3,59,tzinfo=timezone.utc).timestamp()))
    def test_reject(self):
        for x in [XML.replace(b"WAWP",b"WAAA"),XML.replace(b"03:59:00",b"04:01:00"),XML.replace(b"knots",b"m/s"),XML.replace(b'value="0" units="knots"',b'value="5abc" units="knots"'),XML.replace(b'value="0" units="knots"',b'value="-1" units="knots"'),XML.replace(b"</awosnet>",b'<windGust value="1"/></awosnet>'),XML.replace(b'twoMinuteWindSpeed value="0"',b'twoMinuteWindSpeed value="1"'),XML.replace(b'tenMinutedewPoint value="23"',b'tenMinutedewPoint value="22"'),b'<!DOCTYPE a [<!ENTITY x "a">]><a>&x;</a>']:
            with self.subTest(x=x):
                with self.assertRaises(ValueError): a.normalize(x,NOW,confirmed=True)
        with self.assertRaises(ValueError): a.normalize(XML,NOW)
    def test_different_metadata_withheld(self):
        x=XML.replace(b'<airTemperature value="30" units="deg C"/>',b'<airTemperature value="30" units="deg C" time="1"/>')
        self.assertIsNone(a.normalize(x,NOW,confirmed=True)["temperature_c"])
    def test_report_midnight_rollover(self):
        x=XML.replace(b'020350Z',b'012350Z')
        r=a.normalize(x,NOW,confirmed=True)
        self.assertEqual(r["metar_date_utc"],"2026-01-01")
    def test_report_future_withheld(self):
        self.assertNotIn("metar_text",a.normalize(XML.replace(b"020350Z",b"020450Z"),NOW,confirmed=True))
