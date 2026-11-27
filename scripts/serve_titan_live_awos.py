"""Private, on-demand TITAN + AWOSnet bridge, bound to loopback only.

No observation persistence, scheduled collection, public relay or forecast
calculation changes. The public dashboard and its public assets are retrieved
over HTTPS; the station feed itself is unauthenticated HTTP. Run only on an
authorized workstation with a trusted station network and confirmed semantics.
"""
import argparse
import json
import mimetypes
import re
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from normalize_awosnet_xml import NoRedirect, fetch_once, normalize, utc_text

BASE = "https://anzthebest0-cpu.github.io/wawp-forecast-engine/"
ROOT = Path(__file__).resolve().parents[1]
META = '<meta name="titan-awos-proxy" content="">'
MAX_PUBLIC = 64 * 1024 * 1024
ALLOWED_SUFFIXES = {".html", ".js", ".css", ".json", ".jsonl", ".webp", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2"}
SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")

def public_path(path):
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Invalid path")
    parts = path[1:].split("/")
    if parts[-1] == "":
        parts[-1] = "index.html"
    if any(not SEGMENT.fullmatch(p) or p in (".", "..") for p in parts):
        raise ValueError("Invalid path segment")
    rel = "/".join(parts)
    if Path(rel).suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Unsupported public asset")
    return rel

def read_public(rel):
    # No redirects, cookies, authorization headers, or arbitrary upstream URL.
    with build_opener(NoRedirect()).open(Request(BASE + rel, headers={"Accept":"*/*","Cache-Control":"no-cache"}), timeout=20) as res:
        if res.status != 200:
            raise ValueError("Public asset unavailable")
        body = res.read(MAX_PUBLIC + 1)
    if len(body) > MAX_PUBLIC:
        raise ValueError("Public asset too large")
    return body

def local_html(body):
    html = body.decode("utf-8")
    candidates = (META, '<meta name="titan-awos-proxy" content="https://wawp-forecast-engine.anzthebest0.workers.dev/awos">')
    if sum(html.count(tag) for tag in candidates) != 1:
        raise ValueError("Expected exactly one recognized AWOS meta tag")
    for tag in candidates:
        if tag in html:
            return html.replace(tag, '<meta name="titan-awos-proxy" content="/awos">', 1).encode("utf-8")

def live_snapshot():
    body = fetch_once()
    now = datetime.now(timezone.utc)
    sample = normalize(body, now, confirmed=True)
    observed = datetime.strptime(sample["observed_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if now - observed > timedelta(minutes=5):
        raise ValueError("Stale AWOSnet source")
    sample["retrieved_at_utc"] = utc_text(now)
    return sample

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_body(self, status, body, kind):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def error_json(self, status, reason):
        self.send_body(status, json.dumps({"status": reason}).encode(), "application/json")

    def do_GET(self):
        # Reject cross-site and DNS-rebinding requests. No CORS access.
        host = "127.0.0.1:" + str(self.server.server_port)
        if self.headers.get("Host") != host:
            return self.error_json(403, "forbidden")
        p = urlsplit(self.path)
        if p.scheme or p.netloc or p.fragment:
            return self.error_json(400, "invalid_request")
        if p.path == "/awos":
            if p.query or self.headers.get("Sec-Fetch-Site") != "same-origin":
                return self.error_json(403, "forbidden")
            if self.headers.get("Origin") not in (None, "http://" + host):
                return self.error_json(403, "forbidden")
            # One in-flight upstream request, globally at most one per 60 s.
            # No background timer or persistent cache, even with multiple tabs.
            with self.server.awos_lock:
                if time.monotonic() < self.server.awos_next:
                    return self.error_json(429, "request_interval")
                self.server.awos_next = time.monotonic() + 60
                try:
                    payload = live_snapshot()
                except Exception:
                    return self.error_json(502, "awos_unavailable_or_invalid")
            return self.send_body(200, json.dumps(payload, allow_nan=False).encode(), "application/json")
        try:
            rel = public_path(p.path)
            if rel == "observation-ui.js":
                body = (ROOT / "docs" / rel).read_bytes()
            else:
                body = read_public(rel)
            if rel == "index.html":
                body = local_html(body)
            kind = {".js":"text/javascript", ".json":"application/json", ".jsonl":"application/x-ndjson"}.get(Path(rel).suffix) or mimetypes.guess_type(rel)[0] or "application/octet-stream"
            self.send_body(200, body, kind)
        except ValueError:
            self.error_json(404, "asset_unavailable")
        except Exception:
            self.error_json(502, "public_dashboard_unavailable")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-http", action="store_true", help="Permit unauthenticated HTTP source on trusted network")
    p.add_argument("--confirmed-source-semantics", action="store_true", help="Confirm display UTC, source units and true 2-minute wind")
    p.add_argument("--check", action="store_true", help="Validate one snapshot, print status only, then exit")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    if not args.allow_http or not args.confirmed_source_semantics:
        p.error("Authorized trusted-network HTTP and confirmed source semantics required; see docs/TITAN_LOCAL_LIVE_AWOS.md")
    if not 1024 <= args.port <= 65535:
        p.error("Port outside supported range")
    if args.check:
        try:
            live_snapshot()
        except Exception:
            p.exit(1, "AWOSnet snapshot rejected or unavailable; check source and workstation clock.\n")
        print("AWOSNET_LIVE_SNAPSHOT_PASS (station, units, timestamps and freshness; no raw data retained)")
        return
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.awos_lock = threading.Lock()
        server.awos_next = 0.0
        url = "http://127.0.0.1:" + str(args.port) + "/"
        print("Private TITAN: " + url + " | Ctrl+C stops the relay. No observation archive.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
