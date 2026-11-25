# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Configuration: one TOML file plus environment overrides.

The file is $SHEPHERD_CONFIG, else $XDG_CONFIG_HOME/shepherd/config.toml
(default ~/.config/shepherd/config.toml). A missing file is fine as long as
the values a command needs come from the environment. Relative paths in the
file are resolved against the file's directory. See config.example.toml.

Environment overrides:

  SHEPHERD_GERRIT        [gerrit] host
  SHEPHERD_PROJECT       [gerrit] project
  SHEPHERD_OWNER         [gerrit] owner
  SHEPHERD_EXTRA_CLS     [gerrit] extra_cls, comma-separated
  SHEPHERD_AUTH          [gerrit] auth
  SHEPHERD_STATE_DIR     [paths] state_dir
  SHEPHERD_WATCH_DIR     [paths] watch_dir
  SHEPHERD_DATA_DIR      [paths] data_dir
  SHEPHERD_PROJECT_FILE  [paths] project_file
  SHEPHERD_DASH_HOST     [dash] host
  SHEPHERD_DASH_PORT     [dash] port
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

AUTH_MODES = ("auto", "git-credential-luci", "gitcookies")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    file: Path | None
    gerrit: str
    project: str
    owner: str
    extra_cls: tuple
    auth: str
    state_dir: Path
    watch_dir: Path
    data_dir: Path
    project_file: Path | None
    dash_host: str
    dash_port: int
    interval: int
    max_wait: int
    notify: bool

    # Watcher output.
    @property
    def state(self):
        return self.watch_dir / "cl-watch-state.json"

    @property
    def log(self):
        return self.watch_dir / "cl-watch.log"

    # Dashboard data.
    @property
    def cls(self):
        return self.data_dir / "cls.json"

    @property
    def requests(self):
        return self.data_dir / "requests.jsonl"

    @property
    def agent(self):
        return self.data_dir / "agent.json"

    @property
    def experiments(self):
        return self.data_dir / "experiments.json"

    @property
    def pinpoint(self):
        return self.data_dir / "pinpoint.json"

    def change_url(self, num):
        return f"{self.gerrit}/c/{self.project}/+/{num}"

    def require_owner(self):
        if not self.owner:
            raise ConfigError(
                "no Gerrit owner configured: set [gerrit] owner in "
                f"{self.file or default_path()} or SHEPHERD_OWNER"
            )
        return self.owner


def default_path():
    if os.environ.get("SHEPHERD_CONFIG"):
        return Path(os.environ["SHEPHERD_CONFIG"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "shepherd" / "config.toml"


def _default_state_dir():
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
    return Path(base) / "shepherd"


def load(path=None):
    path = Path(path).expanduser() if path else default_path()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        file = path.resolve()
    except FileNotFoundError:
        data, file = {}, None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    base = file.parent if file else Path.cwd()

    def get(section, key, env, default):
        if os.environ.get(env):
            return os.environ[env]
        return data.get(section, {}).get(key, default)

    def path_value(value):
        if not value:
            return None
        p = Path(value).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    extra = get("gerrit", "extra_cls", "SHEPHERD_EXTRA_CLS", [])
    if isinstance(extra, str):
        extra = list(extra.replace(",", " ").split())
    try:
        extra = tuple(int(x) for x in extra)
    except ValueError:
        raise ConfigError(f"extra_cls must be CL numbers: {extra!r}") from None

    auth = get("gerrit", "auth", "SHEPHERD_AUTH", "auto")
    if auth not in AUTH_MODES:
        raise ConfigError(f"auth must be one of {', '.join(AUTH_MODES)}")

    state_dir = (
        path_value(get("paths", "state_dir", "SHEPHERD_STATE_DIR", None))
        or _default_state_dir()
    )
    watch = data.get("watch", {})
    try:
        port = int(get("dash", "port", "SHEPHERD_DASH_PORT", 8787))
        interval = int(watch.get("interval", 300))
        max_wait = int(watch.get("max_wait", 4 * 3600))
    except ValueError as e:
        raise ConfigError(str(e)) from None
    return Config(
        file=file,
        gerrit=get(
            "gerrit",
            "host",
            "SHEPHERD_GERRIT",
            "https://chromium-review.googlesource.com",
        ).rstrip("/"),
        project=get("gerrit", "project", "SHEPHERD_PROJECT", "v8/v8"),
        owner=get("gerrit", "owner", "SHEPHERD_OWNER", ""),
        extra_cls=extra,
        auth=auth,
        state_dir=state_dir,
        watch_dir=path_value(get("paths", "watch_dir", "SHEPHERD_WATCH_DIR", None))
        or state_dir,
        data_dir=path_value(get("paths", "data_dir", "SHEPHERD_DATA_DIR", None))
        or state_dir,
        project_file=path_value(
            get("paths", "project_file", "SHEPHERD_PROJECT_FILE", None)
        ),
        dash_host=get("dash", "host", "SHEPHERD_DASH_HOST", "127.0.0.1"),
        dash_port=port,
        interval=interval,
        max_wait=max_wait,
        notify=bool(watch.get("notify", True)),
    )
