import http.client
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import serve_titan_live_awos as bridge

class LocalBridgeTests(unittest.TestCase):
    def test_public_asset_paths(self):
        self.assertEqual(bridge.public_path("/"), "index.html")
        self.assertEqual(bridge.public_path("/titan/"), "titan/index.html")
        self.assertEqual(bridge.public_path("/data/taf_guidance.json"), "data/taf_guidance.json")
        for path in ("//evil.example/x.js", "/../x.js", "/%2e%2e/x.js", "/a//b.js", "/test.exe"):
            with self.assertRaises(ValueError):
                bridge.public_path(path)

    def test_only_disabled_meta_replaced(self):
        self.assertIn(b'content="/awos"', bridge.local_html(bridge.META.encode()))
        with self.assertRaises(ValueError):
            bridge.local_html(b'<html>other</html>')

    def test_loopback_and_no_cross_site_awos(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler) as server:
            server.awos_lock = threading.Lock()
            server.awos_next = 0.0
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(path, headers):
                    c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    try:
                        c.request("GET", path, headers=headers)
                        r = c.getresponse()
                        return r.status, json.loads(r.read()), r.getheader("Access-Control-Allow-Origin")
                    finally:
                        c.close()
                with patch.object(bridge, "live_snapshot", return_value={"station":"WAWP"}) as sample:
                    self.assertEqual(request("/awos", {"Host":"evil.example", "Sec-Fetch-Site":"same-origin"})[0], 403)
                    self.assertEqual(request("/awos", {"Sec-Fetch-Site":"cross-site"})[0], 403)
                    self.assertEqual(request("/awos", {})[0], 403)
                    self.assertEqual(request("/awos?x=1", {"Sec-Fetch-Site":"same-origin"})[0], 403)
                    sample.assert_not_called()
                    status, data, cors = request("/awos", {"Sec-Fetch-Site":"same-origin"})
                    self.assertEqual((status, data, cors), (200, {"station":"WAWP"}, None))
                    self.assertEqual(request("/awos", {"Sec-Fetch-Site":"same-origin"})[0], 429)
                    sample.assert_called_once()
            finally:
                server.shutdown()
                thread.join(timeout=3)

if __name__ == "__main__":
    unittest.main()
