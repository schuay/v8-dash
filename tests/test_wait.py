# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Waiter exit paths: Gerrit event, dashboard item, idle cap, lock held."""

import json
import unittest

from shepherd import watch
from shepherd.dash import requestlog
from tests import fixtures as fx
from tests.fake_gerrit import FakeGerrit
from tests.util import ScratchCase


class WaitTest(ScratchCase):
    def setUp(self):
        super().setUp()
        self.g = FakeGerrit()
        self.addCleanup(self.g.close)
        self.configure(self.g.url)
        self.g.add(
            fx.change(100001, "[parser] Cache scope lookups", reviewers=[fx.ALICE])
        )
        watch.poll(self.cfg)  # baseline

    def wait(self, *args):
        return self.shepherd("wait", "--interval", "1", *args)

    def test_gerrit_event(self):
        c = self.g.changes[100001]
        c["messages"] = [fx.message(fx.ALICE, "LGTM with a nit")]
        c["updated"] = fx.ago(minutes=1)
        out = self.wait("--max-wait", "30")
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertEqual(result["dash"], [])
        self.assertEqual(len(result["gerrit"]), 1)
        self.assertIn("message from alice: LGTM with a nit", result["gerrit"][0])
        self.assertIn("message from alice", self.cfg.log.read_text())
        agent = json.loads(self.cfg.agent.read_text())
        self.assertEqual(agent["state"], "listening")

    def test_dash_item(self):
        rid = requestlog.new_request(
            self.cfg.requests, "100001", "ping", "Ping alice", by="user"
        )
        out = self.wait("--max-wait", "30")
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertEqual(result["gerrit"], [])
        self.assertEqual([r["id"] for r in result["dash"]], [rid])
        # An item stays in the output until it is closed.
        self.assertEqual(self.wait("--max-wait", "30").returncode, 0)
        requestlog.done(self.cfg.requests, rid, "Pinged")
        self.assertEqual(self.wait("--max-wait", "0.5").returncode, 124)

    def test_idle(self):
        out = self.wait("--max-wait", "0.5")
        self.assertEqual(out.returncode, 124, out.stderr)
        self.assertEqual(out.stdout, "")

    def test_lock_held(self):
        held = watch.lock(self.cfg)
        self.addCleanup(held.close)
        out = self.wait("--max-wait", "30")
        self.assertEqual(out.returncode, 2)
        self.assertIn("another instance holds", out.stderr)

    def test_poll_failure_is_not_fatal(self):
        self.g.close()
        out = self.wait("--max-wait", "0.5")
        self.assertEqual(out.returncode, 124)
        self.assertIn("poll failed", out.stderr)
        self.assertIn("poll failed", self.cfg.log.read_text())


if __name__ == "__main__":
    unittest.main()
