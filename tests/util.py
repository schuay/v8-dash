# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Shared test setup: a scratch directory with a config file."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shepherd import config
from tests import fixtures


class ScratchCase(unittest.TestCase):
    """Each test gets a scratch dir, a config there and a clean environment.

    `gerrit_host` defaults to a placeholder; tests that need Gerrit set it to
    a FakeGerrit URL before calling configure().
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.state_dir = self.dir / "state"
        self.state_dir.mkdir()
        self._env = dict(os.environ)
        for k in list(os.environ):
            if k.startswith("SHEPHERD_"):
                del os.environ[k]
        self.configure()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def configure(self, host="https://gerrit.example.com", **sections):
        path = fixtures.write_config(
            self.dir / "config.toml", self.state_dir, **sections
        )
        if host:
            text = path.read_text().replace('"https://gerrit.example.com"', f'"{host}"')
            path.write_text(text)
        os.environ["SHEPHERD_CONFIG"] = str(path)
        self.cfg = config.load()
        return self.cfg

    def shepherd(self, *args, timeout=60):
        """Runs `python3 -m shepherd ARGS` with the test's environment."""
        return subprocess.run(
            [sys.executable, "-m", "shepherd", *args],
            check=False,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(os.environ),
        )
