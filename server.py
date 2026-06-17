#!/usr/bin/env python3
"""
UC Research Dashboard — Live Collaboration Server
==================================================
Run this on ONE office PC. Everyone on the same network can then open:
    http://<THIS-PC-IP>:8080

How to run:
    python server.py

Requirements: Python 3.6+  (no extra packages needed)
"""

import http.server
import json
import os
import pathlib
import shutil
import socketserver
import sys
import threading
import time
from urllib.parse import urlparse, parse_qs

# ── Configuration ─────────────────────────────────────────────────────────────
PORT        = 8080
DATA_FILE   = "dashboard_data.json"
BACKUP_DIR  = "backups"

def find_html_file():
    """Auto-detect the dashboard HTML file in this folder.
    Looks for 'index.html' or 'UC_Dashboard.html' first (common names),
    then falls back to any .html file found, so renaming the file never breaks the server."""
    for name in ("index.html", "UC_Dashboard.html"):
        if os.path.exists(name):
            return name
    htmls = sorted(pathlib.Path(".").glob("*.html"))
    return str(htmls[0]) if htmls else "UC_Dashboard.html"

HTML_FILE = find_html_file()
# ─────────────────────────────────────────────────────────────────────────────

DATA_LOCK = threading.Lock()

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    # Write to temp file first, then rename — avoids corruption on power loss
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)

def make_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"dashboard_data_{ts}.json")
    shutil.copy2(DATA_FILE, dst)
    # Keep only last 20 backups
    backups = sorted(pathlib.Path(BACKUP_DIR).glob("dashboard_data_*.json"))
    for old in backups[:-20]:
        old.unlink()
    return dst


class DashboardHandler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Quiet down static file spam; show only API calls
        if "/api/" in (args[0] if args else ""):
            print(f"  [{time.strftime('%H:%M:%S')}] {fmt % args}")

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        if path == "/api/data":
            self._json_response(200, load_data())

        elif path == "/api/ping":
            self._json_response(200, {"status": "ok", "time": time.strftime("%H:%M:%S")})

        elif path == "/" or path.lower().endswith(".html"):
            # Serve the dashboard HTML for "/", "/index.html", "/UC_Dashboard.html",
            # or any renamed copy like "/UC_Dashboard (2).html"
            if os.path.exists(HTML_FILE):
                self._serve_file(HTML_FILE, "text/html; charset=utf-8")
            else:
                self._text_response(404, f"Dashboard file '{HTML_FILE}' not found.\n"
                                        f"Make sure {HTML_FILE} is in the same folder as server.py")
        else:
            super().do_GET()

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        if path == "/api/save":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length)
                try:
                    new_data = json.loads(body)
                except json.JSONDecodeError as e:
                    self._json_response(400, {"error": f"Invalid JSON: {e}"})
                    return

                with DATA_LOCK:
                    make_backup()
                    save_data(new_data)

                print(f"  [{time.strftime('%H:%M:%S')}] ✅ Data saved by a client "
                      f"({len(body)} bytes, backup created)")
                self._json_response(200, {"status": "saved", "time": time.strftime("%H:%M:%S")})
            except Exception as e:
                # Never let an unexpected error fall through to an HTML response —
                # the dashboard always expects JSON back from /api/save.
                print(f"  [{time.strftime('%H:%M:%S')}] ❌ Save error: {e}")
                self._json_response(500, {"error": str(e)})

        else:
            self._json_response(404, {"error": f"Unknown endpoint: {path}"})

    # ── OPTIONS (CORS preflight) ───────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _text_response(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",   "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filepath, content_type):
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type",   content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)


def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    # Ensure data file exists
    if not os.path.exists(DATA_FILE):
        print(f"⚠️  '{DATA_FILE}' not found — creating empty data file.")
        save_data({})

    # Ensure HTML file exists
    if not os.path.exists(HTML_FILE):
        print(f"\n⚠️  WARNING: no dashboard .html file found in this folder.")
        print(f"   Put your dashboard HTML file (e.g. index.html) next to server.py\n")

    local_ip = get_local_ip()

    print("=" * 60)
    print("  UC Research Dashboard — Live Server")
    print("=" * 60)
    print(f"\n  ✅ Server running on port {PORT}")
    print(f"  📄 Serving dashboard file: {HTML_FILE}")
    print(f"\n  Open on THIS computer:")
    print(f"     http://localhost:{PORT}")
    print(f"\n  Share with your unit (same office network):")
    print(f"     http://{local_ip}:{PORT}")
    print(f"\n  Data file : {os.path.abspath(DATA_FILE)}")
    print(f"  Backups   : {os.path.abspath(BACKUP_DIR)}/")
    print(f"\n  Press Ctrl+C to stop the server.")
    print("=" * 60 + "\n")


    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n  Server stopped. Goodbye!")
            sys.exit(0)


if __name__ == "__main__":
    main()
