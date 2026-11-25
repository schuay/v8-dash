# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""shepherd drafts against a fake Gerrit, with credentials from a cookie file."""

import json
import os
import subprocess
import sys
import unittest

from shepherd import drafts, gerrit
from tests import fixtures as fx
from tests.fake_gerrit import FakeGerrit
from tests.util import ROOT, ScratchCase

PARENT = {
    "id": "c1",
    "line": 10,
    "patch_set": 2,
    "author": fx.ALICE,
    "message": "Rename this",
    "updated": "2026-01-15 10:00:00.000000000",
    "unresolved": True,
}
REPLY = {
    "id": "c2",
    "line": 10,
    "patch_set": 2,
    "author": fx.OWNER,
    "in_reply_to": "c1",
    "message": "Will do",
    "updated": "2026-01-15 10:05:00.000000000",
    "unresolved": True,
}
OTHER = {
    "id": "c3",
    "patch_set": 1,
    "author": fx.BOB,
    "message": "Nice",
    "updated": "2026-01-14 10:00:00.000000000",
    "unresolved": False,
}


class DraftsTest(ScratchCase):
    def setUp(self):
        super().setUp()
        self.g = FakeGerrit()
        self.addCleanup(self.g.close)
        self.configure(self.g.url, gerrit={"auth": "gitcookies"})
        self.g.comments[100001] = {
            "src/a.cc": [PARENT, REPLY],
            "/PATCHSET_LEVEL": [OTHER],
        }
        # git reads the cookie file location from its global config.
        cookies = self.dir / "gitcookies"
        cookies.write_text("127.0.0.1\tFALSE\t/\tFALSE\t0\to\tgit-you=secret\n")
        gitconfig = self.dir / "gitconfig"
        gitconfig.write_text(f"[http]\n\tcookiefile = {cookies}\n")
        os.environ["GIT_CONFIG_GLOBAL"] = str(gitconfig)
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

    def test_threads(self):
        self.g.drafts[100001] = {
            "src/a.cc": [
                {
                    "id": "d9",
                    "line": 10,
                    "patch_set": 2,
                    "in_reply_to": "c2",
                    "message": "Done",
                    "updated": "2026-01-15 11:00:00.000000000",
                    "unresolved": False,
                }
            ]
        }
        threads = drafts.threads(
            drafts.published(self.cfg, 100001) + drafts.drafts(self.cfg, 100001)
        )
        self.assertEqual(
            [(t["path"], [c["id"] for c in t["comments"]]) for t in threads],
            [("/PATCHSET_LEVEL", ["c3"]), ("src/a.cc", ["c1", "c2", "d9"])],
        )
        # A draft doesn't resolve a thread until it is published.
        self.assertTrue(threads[1]["unresolved"])
        self.assertTrue(threads[1]["comments"][2]["draft"])

    def test_create_reply_inherits_location(self):
        results = drafts.create(
            self.cfg,
            100001,
            [
                {"message": "Renamed in PS3", "in_reply_to": "c1", "unresolved": False},
                {"message": "Top-level summary"},
                {"message": ""},
            ],
        )
        self.assertEqual([r["ok"] for r in results], [True, True, False])
        (rev1, reply), (rev2, top) = self.g.put_bodies
        self.assertEqual(rev1, "2")  # the parent's patchset
        self.assertEqual(
            reply,
            {
                "path": "src/a.cc",
                "message": "Renamed in PS3",
                "unresolved": False,
                "side": "REVISION",
                "line": 10,
                "in_reply_to": "c1",
                "is_ai": True,
            },
        )
        self.assertEqual(rev2, "current")
        self.assertEqual(
            top,
            {
                "path": "/PATCHSET_LEVEL",
                "message": "Top-level summary",
                "unresolved": True,
                "is_ai": True,
            },
        )

    def test_no_credentials(self):
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.dir / "none")
        os.environ["HOME"] = str(self.dir)
        with self.assertRaises(gerrit.AuthError):
            drafts.drafts(self.cfg, 100001)
        # Reads still work and say why drafts are missing.
        out = self.shepherd("drafts", "threads", "100001", "--unresolved")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("drafts not included", out.stderr)
        self.assertEqual([t["path"] for t in json.loads(out.stdout)], ["src/a.cc"])

    def test_cli(self):
        out = subprocess.run(
            [sys.executable, "-m", "shepherd", "drafts", "create", "100001"],
            input=json.dumps([{"message": "Done", "in_reply_to": "c1"}]),
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=dict(os.environ),
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(json.loads(out.stdout)[0]["ok"])
        out = self.shepherd("drafts", "list", "100001")
        self.assertEqual(out.returncode, 0, out.stderr)
        listed = json.loads(out.stdout)["100001"]
        self.assertEqual(
            [(d["path"], d["message"]) for d in listed], [("src/a.cc", "Done")]
        )
        self.assertNotIn("secret", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
