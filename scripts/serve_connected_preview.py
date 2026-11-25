"""Loopback-only, read-only relay for already-public TITAN data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import webbrowser
ROOT=Path(__file__).resolve().parent
BASE="https://anzthebest0-cpu.github.io/wawp-forecast-engine/data/"
FILES={"tafor_intel.json","latest_weights.json","latest_performance.json","taf_guidance.json","climatology.json","individual_models.json","db_health.json","persistency.json","diurnal_climatology.json","system_workflow.json","operational_residuals.json","tafor_shadow.json","weight_history.jsonl"}
LIMIT=32*1024*1024
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
class Handler(BaseHTTPRequestHandler):
    def send_body(self,code,body,kind):
        self.send_response(code);self.send_header("Content-Type",kind);self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        p=urlsplit(self.path)
        if p.path in ("/","/TITAN-connected.html"):
            return self.send_body(200,(ROOT/"TITAN-connected.html").read_bytes(),"text/html; charset=utf-8")
        name=p.path.removeprefix("/data/")
        if p.path!="/data/"+name or name not in FILES: return self.send_body(404,b"Not found","text/plain")
        try:
            url=BASE+name+("?"+p.query if p.query.startswith("t=") and p.query[2:].isdigit() else "")
            with build_opener(NoRedirect()).open(Request(url,headers={"Accept":"application/json,text/plain","Cache-Control":"no-cache"}),timeout=15) as res:
                body=res.read(LIMIT+1)
            if len(body)>LIMIT:raise ValueError("oversize")
            return self.send_body(200,body,"application/json" if name.endswith(".json") else "text/plain; charset=utf-8")
        except Exception: return self.send_body(502,b"Published source unavailable","text/plain")
    def log_message(self,*args): pass
if __name__=="__main__":
    with ThreadingHTTPServer(("127.0.0.1",8766),Handler) as http:
        print("Open http://127.0.0.1:8766/ ; close this console to stop.",flush=True)
        webbrowser.open("http://127.0.0.1:8766/")
        http.serve_forever()
