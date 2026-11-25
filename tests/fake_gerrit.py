# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""A minimal in-process Gerrit for the tests.

Serves the endpoints shepherd uses from dicts the test controls:

  GET /changes/?q=change:A OR change:B&o=...   batched change query
  GET /changes/?q=owner:X status:open ...      the owner's open changes
  GET /changes/N/comments                      published comments
  GET /a/changes/N/drafts                      drafts (needs credentials)
  PUT /a/changes/N/revisions/R/drafts          create a draft
"""

import copy
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

XSSI = ")]}'\n"


class FakeGerrit:
    def __init__(self, owner="you@example.com", cookie="o=git-you=secret"):
        self.owner = owner
        self.cookie = cookie
        self.changes = {}  # num -> ChangeInfo, including messages
        self.comments = {}  # num -> {path: [CommentInfo]}
        self.drafts = {}  # num -> {path: [CommentInfo]}
        self.queries = []  # query strings seen, in order
        self.put_bodies = []
        self._ids = 0
        handler = type("H", (_Handler,), {"gerrit": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add(self, info):
        self.changes[info["_number"]] = info

    def next_id(self):
        self._ids += 1
        return f"d{self._ids}"


class _Handler(BaseHTTPRequestHandler):
    gerrit = None

    def log_message(self, fmt, *args):
        pass

    def _reply(self, status, value=None):
        body = (XSSI + json.dumps(value)).encode() if value is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if self.headers.get("Cookie") == self.gerrit.cookie:
            return True
        self._reply(401)
        return False

    def do_GET(self):
        g = self.gerrit
        url = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(url.query)
        parts = url.path.strip("/").split("/")
        if url.path == "/changes/":
            q = params["q"][0]
            g.queries.append(q)
            if q.startswith("owner:"):
                found = [
                    {"_number": n}
                    for n, c in sorted(g.changes.items())
                    if c["owner"]["email"] == g.owner and c["status"] == "NEW"
                ]
                self._reply(200, found)
                return
            nums = [int(t.split(":")[1]) for t in q.split(" OR ")]
            with_msgs = "MESSAGES" in params.get("o", [])
            out = []
            for n in nums:
                if n in g.changes:
                    c = copy.deepcopy(g.changes[n])
                    if not with_msgs:
                        c.pop("messages", None)
                        c.pop("reviewer_updates", None)
                    out.append(c)
            self._reply(200, out)
        elif len(parts) == 3 and parts[0] == "changes" and parts[2] == "comments":
            self._reply(200, g.comments.get(int(parts[1]), {}))
        elif parts[:2] == ["a", "changes"] and parts[3:] == ["drafts"]:
            if self._authed():
                self._reply(200, g.drafts.get(int(parts[2]), {}))
        else:
            self._reply(404)

    def do_PUT(self):
        g = self.gerrit
        parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
        if not (
            parts[:2] == ["a", "changes"]
            and parts[3] == "revisions"
            and parts[5:] == ["drafts"]
        ):
            self._reply(404)
            return
        if not self._authed():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        g.put_bodies.append((parts[4], body))
        info = {
            **body,
            "id": g.next_id(),
            "patch_set": int(parts[4]) if parts[4].isdigit() else 1,
        }
        g.drafts.setdefault(int(parts[2]), {}).setdefault(body["path"], []).append(info)
        self._reply(201, info)
