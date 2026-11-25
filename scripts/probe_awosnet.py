"""Read-only AWOSnet connectivity probe, run on an authorized workstation.

No upload, no credential requests, no raw payload logging, no dashboard writes.
HTTPS is default; explicitly opt in to HTTP only on a trusted station network.
"""
import argparse
import hashlib
import json
import socket
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HOST = "wawp.awosnet.com"
LIMIT = 128 * 1024

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def inspect_payload(body):
    result = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        result["payload_type"] = "xml_declaration_rejected"
        return result
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        result["payload_type"] = "non_xml_or_malformed"
        return result
    def tagname(tag):
        return tag.rsplit("}", 1)[-1][:80]
    result["payload_type"] = "xml"
    result["root_tag"] = tagname(root.tag)
    result["element_names"] = sorted({tagname(e.tag) for e in root.iter()})[:100]
    return result

def probe(scheme, endpoint, timeout, capture_dir=None):
    url = f"{scheme}://{HOST}/{endpoint}"
    out = {"endpoint": url, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    opener = urllib.request.build_opener(NoRedirect())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TITAN-AWOSnet-connectivity-probe/1.0", "Accept": "application/xml,text/plain,text/html"})
        with opener.open(req, timeout=timeout) as response:
            body = response.read(LIMIT + 1)
            out.update(status=response.status, content_type=response.headers.get_content_type())
            if len(body) > LIMIT:
                out["error"] = "response_exceeds_128KiB"
                return out
            out.update(inspect_payload(body))
            if capture_dir is not None:
                # Exclusive creation, no overwrite. Keep on approved local storage.
                path = capture_dir / f"{scheme}-{endpoint}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.bin"
                with path.open("xb") as f:
                    f.write(body)
                out["local_capture_created"] = True
    except urllib.error.HTTPError as exc:
        out.update(status=exc.code, error="http_error_or_redirect_not_followed")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        out["error"] = type(exc).__name__
        reason = getattr(exc, "reason", None)
        if reason is not None:
            out["reason_type"] = type(reason).__name__
    return out

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-http", action="store_true", help="Only on a trusted station network and with authorization")
    p.add_argument("--timeout", type=float, default=8)
    p.add_argument("--capture-dir", type=Path, help="Optional approved private directory outside this repository")
    args = p.parse_args()
    if not 1 <= args.timeout <= 30:
        p.error("timeout must be 1–30 seconds")
    if args.capture_dir:
        target = args.capture_dir.resolve()
        repo = Path(__file__).resolve().parents[1]
        if target == repo or repo in target.parents:
            p.error("capture directory must be outside repository")
        if not target.is_dir():
            p.error("capture directory must already exist")
        args.capture_dir = target
    try:
        socket.getaddrinfo(HOST, 443)
        dns = "resolved"
    except OSError as exc:
        dns = type(exc).__name__
    schemes = ["https"] + (["http"] if args.allow_http else [])
    results = [probe(s, e, args.timeout, args.capture_dir) for s in schemes for e in ("awiAwosNet.php", "text.php")]
    print(json.dumps({"station": "WAWP", "dns": dns, "results": results, "note": "HTTP success does not establish sensor validity or permission to publish."}, indent=2))

if __name__ == "__main__":
    main()
