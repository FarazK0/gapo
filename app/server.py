"""Local development server.

Run from the app/ directory:

    python3 server.py

Sets STORAGE_BACKEND=local automatically when DATA_BUCKET is not configured,
so no AWS credentials are needed for local dev.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Must happen before importing handler, which imports predictor and bundle.
if not os.environ.get("DATA_BUCKET"):
    os.environ.setdefault("STORAGE_BACKEND", "local")

# Ensure app/ is on the path so `from core import ...` resolves correctly.
sys.path.insert(0, str(Path(__file__).parent))

import handler  # noqa: E402

PORT = int(os.environ.get("PORT", "8000"))
_WEB_INDEX = Path(__file__).parent.parent / "web" / "index.html"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress per-request stdout noise
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = _WEB_INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            event = {"body": body, "isBase64Encoded": False}
            result = handler.predict(event, context=None)
            status = result["statusCode"]
            resp = result["body"].encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"http://localhost:{PORT}/")
    server.serve_forever()
