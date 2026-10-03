#!/usr/bin/env python3
"""Serve the Pages artifact with its project prefix, without an API backend."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=3040)
parser.add_argument("--base-path", default="/Next-Tutor-Agent")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2] / "apps" / "web" / "out"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *handler_args, **kwargs):
        super().__init__(*handler_args, directory=str(root), **kwargs)

    def prepare_path(self):
        path = urlsplit(self.path).path
        if path == args.base_path:
            self.send_response(302)
            self.send_header("Location", args.base_path + "/")
            self.end_headers()
            return False
        if not path.startswith(args.base_path + "/"):
            self.send_error(404)
            return False
        self.path = self.path[len(args.base_path):]
        return True

    def do_GET(self):
        if self.prepare_path():
            super().do_GET()

    def do_HEAD(self):
        if self.prepare_path():
            super().do_HEAD()


ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
