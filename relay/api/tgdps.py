"""
TGDPS relay on Vercel, pinned to Mumbai (bom1, see ../vercel.json).

TGDPS (tgdps.telangana.gov.in) answers only from India, so GitHub's runners ask this function instead:

    https://<project>.vercel.app/api/tgdps?k=<KEY>&p=liverain.jsp
    https://<project>.vercel.app/api/tgdps?k=<KEY>&p=values.jsp&s1=10801

KEY is set in Vercel: Project -> Settings -> Environment Variables.
"""
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

BASE = "https://tgdps.telangana.gov.in/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36 (MasRainman rainfall report)"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        key = os.environ.get("KEY", "")
        if not key or q.get("k") != key:
            return self._send(403, "no")
        p = q.get("p", "liverain.jsp")
        if not re.fullmatch(r"[\w/.]+\.jsp", p) or ".." in p:
            return self._send(400, "bad page")
        if q.get("s1", "").isdigit():
            p += "?s1=" + q["s1"]
        req = urllib.request.Request(BASE + p, headers={"User-Agent": UA})
        try:
            r = urllib.request.urlopen(req, timeout=55)
            code, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read()
        except Exception as e:
            code, body = 504, f"TGDPS did not answer: {e}".encode()
        self._send(code, body)

    def _send(self, code, body):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)
