# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Dashboard: buckets, request lifecycle, curation and the HTTP surface."""

import http.client
import json
import threading
import unittest

from shepherd.dash import curation, render, requestlog, server, store
from tests import fixtures as fx
from tests.util import ScratchCase


class DashCase(ScratchCase):
    extras = True

    def setUp(self):
        super().setUp()
        fx.write_demo(self.dir, with_extras=self.extras)
        self.srv = server.make_server(self.cfg, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, data, headers or {})
        r = conn.getresponse()
        text = r.read().decode()
        conn.close()
        return r.status, text, r

    def get(self, path):
        status, text, _ = self.request("GET", path)
        self.assertEqual(status, 200, text[:500])
        return text

    def post(self, path, body, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["X-Dash"] = "1"
        status, text, _ = self.request("POST", path, body, headers)
        return status, json.loads(text) if text else None

    def ctl(self, *args):
        return self.shepherd("dashctl", *args)

    def ctl_json(self, *args):
        out = self.ctl(*args)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def rows(self):
        _, m = store.load(self.cfg)
        return (
            {b["key"]: [r["num"] for r in b["rows"]] for b in m["buckets"]},
            {r["num"]: r for b in m["buckets"] for r in b["rows"]},
        )


class BoardTest(DashCase):
    def test_buckets(self):
        buckets, by_num = self.rows()
        expect = {
            "todo": ["100002", "100001"],  # failed run first
            "ping": ["100003"],
            "send": ["100004"],
            "approved": ["100005"],
            "review": ["100006"],
            "decide": ["100007"],
            "untracked": ["100008"],
            "wip": ["100009"],
            "landed": ["100010"],
            "abandoned": ["100011"],
        }
        self.assertEqual(buckets, expect)
        self.assertTrue(by_num["100003"]["curated"])
        self.assertFalse(by_num["100001"]["curated"])
        self.assertEqual(by_num["100006"]["needs"], ["Code-Owners"])
        self.assertEqual(by_num["100009"]["exp"]["id"], "exp-03")
        self.assertEqual(by_num["100001"]["exp"]["id"], "exp-01")

    def test_page(self):
        page = self.get("/")
        for text in (
            "Your move",
            "Waiting on reviewers",
            "Untracked",
            "exp-01",
            "no agent seen",
            "Experiments",
            "Watcher log",
            "Pinpoint synced",
        ):
            self.assertIn(text, page)
        self.assertIn(
            'href="https://gerrit.example.com/c/example/project/+/100001"', page
        )
        self.assertNotIn('class="request pending"', page)
        # Experiment bots come from the data.
        self.assertIn(
            'data-sort="num">linux</th><th class="num" data-sort="num">mac</th>', page
        )
        _, _, r = self.request("GET", "/board")
        self.assertEqual(r.getheader("X-Dash-Version"), store.version(self.cfg))

    def test_static_and_missing(self):
        self.assertIn("applyState", self.get("/static/dash.js"))
        for path in ("/static/nope.js", "/static/../server.py", "/nope"):
            self.assertEqual(self.request("GET", path)[0], 404, path)

    def test_foreign_host_rejected(self):
        status, _, _ = self.request("GET", "/", headers={"Host": "evil.example"})
        self.assertEqual(status, 421)
        status, _, _ = self.request(
            "GET", "/", headers={"Host": f"localhost:{self.port}"}
        )
        self.assertEqual(status, 200)

    def test_pinpoint(self):
        page = self.get("/board")
        self.assertIn('class="pp-sum" data-pp="100001"', page)
        pp = self.get("/pp/100001")
        self.assertIn("chg%", pp)
        self.assertNotIn("\x1b", pp)
        self.assertIn('<span class="dim">bot:</span>', pp)
        self.assertIn("build failed", pp)
        self.assertEqual(self.request("GET", "/pp/1")[0], 404)
        self.assertEqual(
            render.ansi("\x1b[2ma<\x1b[0m \x1b[1;32m+1%\x1b[0mb"),
            '<span class="dim">a&lt;</span> <span class="b up">+1%</span>b',
        )

    def test_escaping(self):
        state = curation.read_json(self.cfg.state, {})
        state["100001"]["subject"] = '<script>alert("x")</script>'
        curation.write_json(self.cfg.state, state)
        page = self.get("/board")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", page)


class NoExtrasTest(DashCase):
    extras = False

    def test_without_optional_files(self):
        page = self.get("/")
        self.assertIn("Your move", page)
        self.assertNotIn("Experiments", page)
        self.assertNotIn("Pinpoint synced", page)
        # No experiment table or Pinpoint column without their data files.
        for text in ("experiment", 'class="pp', "jetstream"):
            self.assertNotIn(text, page.lower())
        self.assertIn('<td colspan="9">', page)
        self.assertEqual(self.request("GET", "/pp/100001")[0], 404)


class RequestTest(DashCase):
    def test_request_lifecycle(self):
        v0 = self.get("/version")
        rid = self.ctl_json(
            "request",
            "--cl",
            "100006",
            "--kind",
            "subagent",
            "Fix <b>the</b> compile error",
        )["id"]
        self.assertNotEqual(self.get("/version"), v0)
        page = self.get("/board")
        self.assertIn('class="request pending"', page)
        self.assertIn("Fix &lt;b&gt;the&lt;/b&gt; compile error", page)
        # A row with an open request sorts first in its bucket.
        state = curation.read_json(self.cfg.state, {})
        state["100005"]["cr_votes"] = {}
        state["100005"]["unsatisfied"] = ["Code-Review"]
        curation.write_json(self.cfg.state, state)
        buckets, _ = self.rows()
        self.assertEqual(buckets["review"][0], "100006", buckets["review"])

        # Nothing approved yet.
        self.assertEqual(self.ctl("wait", "--timeout", "0.1").returncode, 124)
        self.assertIn("agent listening", self.get("/board"))
        self.assertNotEqual(self.ctl("done", rid, "x").returncode, 0)

        url = f"/api/requests/{rid}/decision"
        self.assertEqual(self.post(url, {"decision": "approved"}, auth=False)[0], 400)
        self.assertEqual(self.post(url, {"decision": "maybe"})[0], 409)
        self.assertEqual(
            self.post("/api/requests/nope/decision", {"decision": "approved"})[0], 404
        )
        self.assertEqual(
            self.post(url, {"decision": "approved", "reply": "Rebase first"})[0], 200
        )
        self.assertEqual(self.post(url, {"decision": "rejected"})[0], 409)

        approved = self.ctl_json("wait", "--timeout", "5")
        self.assertEqual([r["id"] for r in approved], [rid])
        self.assertEqual(approved[0]["reply"], "Rebase first")
        self.assertIn("waiting for agent", self.get("/board"))
        self.ctl_json("heartbeat", "fixing 100006")
        self.assertIn("agent working: fixing 100006", self.get("/board"))
        self.ctl_json("done", rid, "Uploaded PS3")
        self.assertEqual(self.ctl("wait", "--timeout", "0.1").returncode, 124)
        page = self.get("/board")
        self.assertIn("Uploaded PS3", page)
        self.assertNotIn("waiting for agent", page)

    def test_user_request(self):
        bad = [
            {"kind": "subagent", "summary": ""},
            {"kind": "nope", "summary": "x"},
            {"kind": "question", "summary": "x"},
            {"kind": "ping", "summary": "x", "cl": "1; rm"},
            {"kind": "ping", "summary": "x" * 2001},
        ]
        for body in bad:
            self.assertEqual(self.post("/api/requests", body)[0], 400, body)
        self.assertEqual(
            self.post("/api/requests", {"kind": "ping", "summary": "x"}, auth=False)[0],
            400,
        )
        status, body = self.post(
            "/api/requests", {"cl": "100003", "kind": "ping", "summary": "Ping alice"}
        )
        self.assertEqual(status, 200)
        req = requestlog.load(self.cfg.requests).requests[body["id"]]
        self.assertEqual((req["status"], req["by"]), ("approved", "user"))
        self.assertEqual(
            [q["id"] for q in self.ctl_json("wait", "--timeout", "5")], [req["id"]]
        )
        self.ctl_json("done", req["id"], "Pinged")

    def test_questions_and_notes(self):
        qid = self.ctl_json("ask", "--cl", "100006", "Keep the fast path?")["id"]
        page = self.get("/board")
        self.assertIn("Keep the fast path?", page)
        self.assertIn(">Answer</button>", page)
        url = f"/api/requests/{qid}/decision"
        # A question can't be rejected or answered with nothing.
        for body in (
            {"decision": "rejected"},
            {"decision": "approved"},
            {"decision": "approved", "reply": "  "},
        ):
            self.assertEqual(self.post(url, body)[0], 409, body)
        self.assertEqual(
            self.post(url, {"decision": "approved", "reply": "Drop it"})[0], 200
        )
        answered = self.ctl_json("wait", "--timeout", "5")
        self.assertEqual([(q["id"], q["reply"]) for q in answered], [(qid, "Drop it")])
        self.assertIn("answered, waiting for agent", self.get("/board"))
        self.ctl_json("done", qid, "Dropped in PS3")

        nid = self.ctl_json("note", "--cl", "100006", "PS2 dry run is green")["id"]
        self.assertIn("PS2 dry run is green", self.get("/board"))
        # A note doesn't wake the agent.
        self.assertEqual(self.ctl("wait", "--timeout", "0.1").returncode, 124)
        url = f"/api/notes/{nid}/dismiss"
        self.assertEqual(self.post(url, {}, auth=False)[0], 400)
        self.assertEqual(self.post("/api/notes/nope/dismiss", {})[0], 404)
        self.assertEqual(self.post(url, {})[0], 200)
        self.assertNotIn("PS2 dry run is green", self.get("/board"))

    def test_list(self):
        rid = self.ctl_json("request", "--kind", "other", "Anything")["id"]
        self.assertEqual(
            [r["id"] for r in self.ctl_json("list", "--status", "pending")], [rid]
        )
        self.assertEqual(self.ctl_json("list", "--status", "done"), [])


class CurationTest(DashCase):
    def test_stale_curation(self):
        cl = "100003"
        self.assertEqual(self.ctl_json("stale"), [])
        buckets, by_num = self.rows()
        self.assertIn(cl, buckets["ping"])

        # The reviewer votes: the ping entry no longer describes the CL.
        state = curation.read_json(self.cfg.state, {})
        state[cl]["cr_votes"] = {"alice": 1}
        state[cl]["unsatisfied"] = []
        curation.write_json(self.cfg.state, state)
        buckets, by_num = self.rows()
        self.assertEqual(by_num[cl]["stale"], ["cr_votes"])
        self.assertIn(cl, buckets["approved"])
        self.assertFalse(by_num[cl]["curated"])
        self.assertIn(">stale</span>", self.get("/board"))
        self.assertEqual(
            [(s["cl"], s["changed"]) for s in self.ctl_json("stale")],
            [(cl, ["cr_votes"])],
        )

        # A vote alone is not approval while a submit requirement is open.
        state[cl]["unsatisfied"] = ["Code-Owners", "No-Unresolved-Comments"]
        curation.write_json(self.cfg.state, state)
        buckets, by_num = self.rows()
        self.assertIn(cl, buckets["review"])
        self.assertEqual(by_num[cl]["needs"], ["Code-Owners"])
        self.assertIn("needs Code-Owners</span>", self.get("/board"))

        # Curating again stamps the new state.
        entry = self.ctl_json(
            "curate",
            "--cl",
            cl,
            "--bucket",
            "review",
            "--next",
            "alice +1; needs an owner",
        )
        self.assertEqual(entry["basis"]["cr_votes"], {"alice": 1})
        self.assertEqual(self.ctl_json("stale"), [])
        self.assertNotEqual(self.ctl("curate", "--cl", "1").returncode, 0)

    def test_untracked_until_curated(self):
        buckets, _ = self.rows()
        self.assertIn("100008", buckets["untracked"])
        self.ctl_json("curate", "--cl", "100008", "--next", "Look at it")
        buckets, _ = self.rows()
        self.assertNotIn("100008", buckets["untracked"])
        self.assertIn("100008", buckets["review"])

    def test_merged_ignores_curation(self):
        self.ctl_json("curate", "--cl", "100010", "--bucket", "ping")
        state = curation.read_json(self.cfg.state, {})
        cls = curation.read_json(self.cfg.cls, {})
        # Stamped against the merged state, so not stale, yet still landed.
        self.assertEqual(curation.changed_fields(cls["100010"], state["100010"]), [])
        self.assertIn("100010", self.rows()[0]["landed"])

    def test_prio(self):
        self.ctl_json("curate", "--cl", "100001", "--prio", "1")
        self.assertEqual(self.rows()[0]["todo"], ["100001", "100002"])


if __name__ == "__main__":
    unittest.main()
