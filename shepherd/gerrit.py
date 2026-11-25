# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Gerrit REST access.

Reads are anonymous. Draft comments need the user's own credentials, which
are read at call time and never stored:

  git-credential-luci  `git-credential-luci get` (depot_tools), sent as a
                       bearer token
  gitcookies           the cookie for the Gerrit host in the file named by
                       `git config http.cookiefile` (default ~/.gitcookies)
"""

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 60
XSSI = ")]}'"


class AuthError(Exception):
    pass


def parse(body):
    """Decodes a Gerrit JSON response, dropping the XSSI guard line."""
    text = body.decode("utf-8")
    if text.startswith(XSSI):
        text = text.split("\n", 1)[1]
    return json.loads(text)


def get(url):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return parse(r.read())


def _git_credential_luci():
    try:
        out = subprocess.run(
            ["git-credential-luci", "get"],
            input="",
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except FileNotFoundError:
        raise AuthError("git-credential-luci is not on PATH (depot_tools)") from None
    except subprocess.CalledProcessError:
        raise AuthError(
            "git-credential-luci holds no credentials; run `git-credential-luci login`"
        ) from None
    for line in out.splitlines():
        if line.startswith("password="):
            return {"Authorization": "Bearer " + line[len("password=") :]}
    raise AuthError("git-credential-luci returned no token")


def _cookiefile():
    try:
        out = subprocess.run(
            ["git", "config", "--path", "http.cookiefile"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except FileNotFoundError:
        out = ""
    return out or os.path.expanduser("~/.gitcookies")


def _gitcookies(url):
    """The Cookie header for `url` from the Netscape-format cookie file.

    http.cookiejar.MozillaCookieJar would need the file's magic first line,
    which cookie files written for git usually lack.
    """
    host = urllib.parse.urlsplit(url).hostname
    path = _cookiefile()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise AuthError(f"cannot read the git cookie file: {e}") from None
    for raw in lines:
        line = raw.removeprefix("#HttpOnly_")
        fields = line.split("\t")
        if line.startswith("#") or len(fields) != 7:
            continue
        domain, name, value = fields[0].lower(), fields[5], fields[6]
        if host == domain.lstrip(".") or host.endswith("." + domain.lstrip(".")):
            return {"Cookie": f"{name}={value}"}
    raise AuthError(f"the git cookie file has no cookie for {host}")


def auth_headers(mode, url):
    """Credentials for `url`, per the configured auth mode."""
    if mode == "git-credential-luci":
        return _git_credential_luci()
    if mode == "gitcookies":
        return _gitcookies(url)
    errors = []
    for fn in (_git_credential_luci, lambda: _gitcookies(url)):
        try:
            return fn()
        except AuthError as e:
            errors.append(str(e))
    raise AuthError("no Gerrit credentials: " + "; ".join(errors))


def authed(cfg, method, path, body=None):
    """An authenticated request against /a/<path>; returns the parsed body."""
    url = f"{cfg.gerrit}/a/{path.lstrip('/')}"
    headers = {"Accept": "application/json", **auth_headers(cfg.auth, url)}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return parse(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace").strip()[:300]
        if e.code in (401, 403):
            raise AuthError(f"Gerrit returned {e.code}: {detail}") from None
        raise RuntimeError(f"Gerrit returned {e.code}: {detail}") from None
