"""
A small HTTP server for the page in web/. Standard library only.

Routes
    GET  /                 the page, with a per-run token baked in
    GET  /api/state        disk numbers plus the current scan snapshot
    POST /api/scan         start a scan
    POST /api/clean        {"ids": [...]}  clean those items, one by one
    POST /api/reveal       {"id": ...}     show it in Finder

The server listens on 127.0.0.1 only. POST requests need the token the page
was served with, so a random website open in another tab cannot drive it.
"""

import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import actions, scan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
TOKEN = secrets.token_urlsafe(24)
SCAN = scan.Scan()

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
         ".png": "image/png", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "tidy"

    def log_message(self, fmt, *args):
        if "-v" in sys.argv:
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    # --- helpers

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, replace=None):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if replace:
            for k, v in replace.items():
                body = body.replace(k.encode(), v.encode())
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def authorised(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost"):
            return False
        if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            return False
        return self.headers.get("X-Tidy-Token") == TOKEN

    # --- routes

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.send_file(os.path.join(WEB, "index.html"), {"__TOKEN__": TOKEN})
        elif path == "/api/state":
            self.send_json(dict(disk=scan.disk(), scan=SCAN.snapshot()))
        elif path.startswith("/static/"):
            name = os.path.basename(path)  # flat folder, no traversal
            self.send_file(os.path.join(WEB, name))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self.authorised():
            self.send_json(dict(error="not allowed"), HTTPStatus.FORBIDDEN)
            return
        path = self.path.split("?")[0]
        body = self.read_json()

        if path == "/api/scan":
            SCAN.start()
            self.send_json(dict(ok=True))

        elif path == "/api/clean":
            results = []
            for item_id in body.get("ids") or []:
                item = SCAN.get(item_id)
                if not item:
                    results.append(dict(id=item_id, ok=False, error="unknown item, rescan first"))
                    continue
                before = item.get("size") or 0
                if item.get("reclaimable") is not None:
                    before = item["reclaimable"]
                try:
                    info = actions.clean(item)
                    after = SCAN.remeasure(item_id)
                    now = after.get("size") or 0
                    if after.get("reclaimable") is not None:
                        now = after["reclaimable"]
                    results.append(dict(id=item_id, ok=True, freed=max(before - now, 0),
                                        item=after, **info))
                except actions.Refused as e:
                    results.append(dict(id=item_id, ok=False, error=str(e)))
                except Exception as e:
                    results.append(dict(id=item_id, ok=False, error=f"{type(e).__name__}: {e}"))
            self.send_json(dict(results=results, disk=scan.disk()))

        elif path == "/api/reveal":
            item = SCAN.get(body.get("id"))
            self.send_json(dict(ok=bool(item and actions.reveal(item))))

        else:
            self.send_error(HTTPStatus.NOT_FOUND)


def free_port(start):
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("no free port between %d and %d" % (start, start + 50))


def main(argv):
    port = 8765
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    port = free_port(port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = "http://127.0.0.1:%d/" % port
    print("tidy is running at", url, flush=True)
    print("press ctrl-c to stop", flush=True)
    SCAN.start()
    if "--no-browser" not in argv:
        threading.Timer(0.4, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
