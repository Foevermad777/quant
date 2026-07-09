from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dashboard.data import DashboardPaths, build_overview


STATIC_DIR = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


class DashboardHandler(BaseHTTPRequestHandler):
    paths: DashboardPaths

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_static("index.html")
            return
        if parsed.path == "/api/overview":
            self._send_json(build_overview(self.paths))
            return
        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path.removeprefix("/static/"))
            return
        self.send_error(404, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, relative_path: str) -> None:
        target = (STATIC_DIR / relative_path).resolve()
        if not target.is_file() or STATIC_DIR not in target.parents:
            self.send_error(404, "Not found")
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_handler(paths: DashboardPaths) -> type[DashboardHandler]:
    class ConfiguredDashboardHandler(DashboardHandler):
        pass

    ConfiguredDashboardHandler.paths = paths
    return ConfiguredDashboardHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local quant dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    paths = DashboardPaths.from_project_root(args.project_root)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(paths))
    print(f"dashboard_url=http://{args.host}:{args.port}")
    print(f"project_root={paths.project_root}")
    server.serve_forever()


if __name__ == "__main__":
    main()

