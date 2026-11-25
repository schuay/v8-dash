# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Config file, defaults and environment overrides."""

import os
import unittest
from pathlib import Path

from shepherd import config
from tests.util import ScratchCase


class ConfigTest(ScratchCase):
    def test_file(self):
        cfg = self.configure(gerrit={"extra_cls": [5, 6]}, dash={"port": 9000})
        self.assertEqual(cfg.owner, "you@example.com")
        self.assertEqual(cfg.extra_cls, (5, 6))
        self.assertEqual(cfg.dash_port, 9000)
        self.assertEqual(cfg.dash_host, "127.0.0.1")
        self.assertEqual(cfg.state, self.state_dir / "cl-watch-state.json")
        self.assertEqual(cfg.requests, self.state_dir / "requests.jsonl")
        self.assertEqual(
            cfg.change_url(7), "https://gerrit.example.com/c/example/project/+/7"
        )

    def test_relative_paths_and_split_dirs(self):
        self.configure(
            paths={"state_dir": "s", "watch_dir": "w", "project_file": "project.md"}
        )
        cfg = config.load()
        base = Path(self.dir).resolve()
        self.assertEqual(cfg.state_dir, base / "s")
        self.assertEqual(cfg.data_dir, base / "s")
        self.assertEqual(cfg.state, base / "w" / "cl-watch-state.json")
        self.assertEqual(cfg.project_file, base / "project.md")

    def test_env_overrides(self):
        os.environ.update(
            {
                "SHEPHERD_OWNER": "other@example.com",
                "SHEPHERD_EXTRA_CLS": "1, 2",
                "SHEPHERD_DASH_PORT": "1234",
                "SHEPHERD_DATA_DIR": str(self.dir / "d"),
            }
        )
        cfg = config.load()
        self.assertEqual(cfg.owner, "other@example.com")
        self.assertEqual(cfg.extra_cls, (1, 2))
        self.assertEqual(cfg.dash_port, 1234)
        self.assertEqual(cfg.cls, self.dir / "d" / "cls.json")
        self.assertEqual(cfg.state, self.state_dir / "cl-watch-state.json")

    def test_missing_file_and_errors(self):
        os.environ["SHEPHERD_CONFIG"] = str(self.dir / "absent.toml")
        os.environ["XDG_STATE_HOME"] = str(self.dir / "xdg")
        cfg = config.load()
        self.assertIsNone(cfg.file)
        self.assertEqual(cfg.state_dir, self.dir / "xdg" / "shepherd")
        with self.assertRaises(config.ConfigError):
            cfg.require_owner()
        out = self.shepherd("watch", "--once")
        self.assertEqual(out.returncode, 1)
        self.assertIn("no Gerrit owner configured", out.stderr)

        bad = self.dir / "bad.toml"
        bad.write_text("[gerrit]\nauth = 'password'\n")
        with self.assertRaises(config.ConfigError):
            config.load(bad)
        bad.write_text("[gerrit\n")
        with self.assertRaises(config.ConfigError):
            config.load(bad)


if __name__ == "__main__":
    unittest.main()
