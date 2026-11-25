# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""The dashboard server: a local, single-user page over the data files.

  shepherd dash [--host H] [--port P]
  python3 -m shepherd.dash

Binds to the configured host (default 127.0.0.1) and has no
authentication. It reads the data files and appends to the request log; it
never talks to Gerrit or Pinpoint. The page polls /version and swaps in
/board when a source file changed.
"""

import argparse
import contextlib
import json
import mimetypes
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import config
from . import render, requestlog, store

STATIC = Path(__file__).parent / "static"
MAX_BODY = 16 * 1024
MAX_TEXT = 2000
LOCAL_NAMES = {"localhost", "127.0.0.1", "[::1]"}


class Handler(BaseHTTPRequestHandler):
    cfg = None  # Set by serve().
    server_version = "shepherd"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, ctype, headers=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value, status=HTTPStatus.OK):
        self._send(status, json.dumps(value), "application/json")

    def _bad(self, msg, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": msg}, status)

    def _host_ok(self):
        """Rejects requests whose Host header names another site, so a page
        that rebinds its DNS name to this address can't read the board."""
        host = (self.headers.get("Host") or "").lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        allowed = LOCAL_NAMES | {self.cfg.dash_host.lower()}
        if name in allowed:
            return True
        self._send(HTTPStatus.MISDIRECTED_REQUEST, "unknown host", "text/plain")
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        path = self.path.split("?", 1)[0]
        cfg = self.cfg
        if path in ("/", "/board"):
            version, model = store.load(cfg)
            board = render.board(model, cfg)
            if path == "/":
                self._send(
                    HTTPStatus.OK,
                    render.page(version, board, bool(model["experiments"])),
                    "text/html; charset=utf-8",
                )
            else:
                self._send(
                    HTTPStatus.OK,
                    board,
                    "text/html; charset=utf-8",
                    [("X-Dash-Version", version)],
                )
        elif path == "/version":
            self._send(HTTPStatus.OK, store.version(cfg), "text/plain")
        elif m := re.fullmatch(r"/pp/(\d+)", path):
            _, model = store.load(cfg)
            batch = model["pinpoint"].get(m.group(1))
            if batch is None:
                self._send(HTTPStatus.NOT_FOUND, "", "text/plain")
            else:
                self._send(
                    HTTPStatus.OK, render.pinpoint(batch), "text/html; charset=utf-8"
                )
        elif m := re.fullmatch(r"/static/([\w.-]+)", path):
            f = STATIC / m.group(1)
            if not f.is_file():
                self._send(HTTPStatus.NOT_FOUND, "", "text/plain")
                return
            ctype = mimetypes.guess_type(f.name)[0] or "text/plain"
            self._send(HTTPStatus.OK, f.read_bytes(), ctype)
        else:
            self._send(HTTPStatus.NOT_FOUND, "not found", "text/plain")

    def _body(self):
        """A mutation's JSON body, or None.

        The custom header forces a CORS preflight, so other origins can't
        post here from the user's browser.
        """
        if self.headers.get("X-Dash") != "1":
            return None
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n > MAX_BODY:
            return None
        try:
            body = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def do_POST(self):
        if not self._host_ok():
            return
        body = self._body()
        if body is None:
            self._bad("bad request")
            return
        path = self.path.split("?", 1)[0]
        log = self.cfg.requests
        if path == "/api/requests":
            self._create(log, body)
        elif m := re.fullmatch(r"/api/requests/([\w-]+)/decision", path):
            self._decide(log, m.group(1), body)
        elif m := re.fullmatch(r"/api/notes/([\w-]+)/dismiss", path):
            try:
                requestlog.dismiss(log, m.group(1))
            except KeyError:
                self._bad("no such note", HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True})
        else:
            self._bad("not found", HTTPStatus.NOT_FOUND)

    def _create(self, log, body):
        summary = _text(body, "summary", required=True)
        if summary is None:
            self._bad(f"summary must be 1..{MAX_TEXT} chars")
            return
        cl = body.get("cl")
        if cl is not None and not str(cl).isdigit():
            self._bad("bad cl")
            return
        try:
            rid = requestlog.new_request(log, cl, body.get("kind"), summary, by="user")
        except ValueError as e:
            self._bad(str(e))
            return
        self._json({"id": rid})

    def _decide(self, log, rid, body):
        reply = _text(body, "reply")
        if reply is None:
            self._bad(f"reply must be at most {MAX_TEXT} chars")
            return
        try:
            requestlog.decide(log, rid, body.get("decision"), reply)
        except KeyError:
            self._bad("no such request", HTTPStatus.NOT_FOUND)
            return
        except ValueError as e:
            self._bad(str(e), HTTPStatus.CONFLICT)
            return
        self._json({"ok": True})


def _text(body, key, required=False):
    """A text field of at most MAX_TEXT chars, or None if invalid."""
    text = str(body.get(key) or "").strip()
    if len(text) > MAX_TEXT or (required and not text):
        return None
    return text


def make_server(cfg, host=None, port=None):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    handler = type("BoundHandler", (Handler,), {"cfg": cfg})
    return ThreadingHTTPServer(
        (host or cfg.dash_host, cfg.dash_port if port is None else port), handler
    )


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="shepherd dash",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    args = ap.parse_args(argv)
    cfg = config.load()
    server = make_server(cfg, args.host, args.port)
    host, port = server.server_address[:2]
    print(
        f"serving http://{'localhost' if host == '127.0.0.1' else host}:"
        f"{port}/ from {cfg.data_dir}",
        file=sys.stderr,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0
