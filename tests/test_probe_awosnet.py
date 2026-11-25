import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("probe_awosnet", Path(__file__).resolve().parents[1] / "scripts" / "probe_awosnet.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class TestProbe(unittest.TestCase):
    def test_xml_summary_does_not_log_values(self):
        r = probe.inspect_payload(b"<awos><airportIdentifier>WAWP</airportIdentifier><METAR>PRIVATE</METAR></awos>")
        self.assertEqual(r["payload_type"], "xml")
        self.assertIn("METAR", r["element_names"])
        self.assertNotIn("PRIVATE", str(r))
        self.assertNotIn("WAWP", str(r))
    def test_entity_rejected(self):
        self.assertEqual(probe.inspect_payload(b'<!DOCTYPE a [<!ENTITY x "a">]><a>&x;</a>')["payload_type"], "xml_declaration_rejected")
    def test_non_xml(self):
        self.assertEqual(probe.inspect_payload(b"METAR WAWP ...")["payload_type"], "non_xml_or_malformed")
