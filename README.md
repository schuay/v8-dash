# shepherd

A local dashboard for keeping many Gerrit CLs moving at once. It groups
your open CLs by what each needs next (your move, ping a reviewer, ready to
send, approved, waiting, keep or abandon) with their dry-run state, votes,
open threads and a curated next step, and it keeps up as Gerrit changes.

A coding agent can do the legwork: a watcher wakes it on dry-run results,
review comments, votes and merges; it fixes simple problems, drafts replies
(never publishes) and keeps the board current. Anything that needs you
shows up on the dashboard as a request to approve or a question to answer.
Without an agent the board still works: buckets follow Gerrit state, and
the watcher log shows what changed.

Python 3.11+ standard library only. Works with any coding agent that can
run shell commands.

## Setup prompt for agents

Paste this into your coding agent from a clone of this repository:

```
Set up shepherd from this repository for me:
1. Read README.md and SECURITY.md.
2. Ask me for my Gerrit account email (the owner of the CLs to track) and,
   optionally, a state directory and dashboard port.
3. Run ./install.sh --non-interactive --owner <email> [--state-dir DIR]
   [--port N]. If it reports missing prerequisites, tell me what to install
   instead of installing system packages yourself.
4. Show me the config file it wrote and ask whether to add CLs owned by
   others (extra_cls) or a project file (project_file, see
   prompt/project.example.md). Edit the config only with my answers.
5. Start the dashboard with the command install.sh printed, check that the
   URL it printed responds, and give me the URL.
6. Then read prompt/shepherd.md (and adapters/<your harness>.md if there is
   one) and start the shepherding loop.
```

## Five-minute setup by hand

1. **Install.** `./install.sh --owner you@example.com` asks for the rest
   (state directory, dashboard port), writes `~/.config/shepherd/config.toml`
   from `config.example.toml`, creates the state directory and a venv in
   it, and runs one Gerrit poll to seed the state. It checks for
   `python3` >= 3.11, `git` and `git cl` (depot_tools) and reports what is
   missing. Re-running it changes nothing; `--force` rewrites the config,
   `--service` adds a user-level launchd agent (macOS) or systemd unit
   (Linux) for the dashboard, `--uninstall [--purge]` removes it all again.
   Add `<state_dir>/venv/bin` to `PATH`, or use `python3 -m shepherd` from
   the checkout.
2. **Credentials** are only needed for draft replies. `shepherd drafts`
   uses `git-credential-luci` (depot_tools; `git-credential-luci login`) or
   the git cookie file you already use for `git cl upload`. Reads need no
   login.
3. **Dashboard.** `shepherd dash`, then open `http://localhost:8787/`.
4. **Watcher.** The agent runs `shepherd wait` in its loop. To watch without
   an agent: `shepherd watch` (polls every 5 minutes, desktop notification
   per event).
5. **Agent.** Point it at `prompt/shepherd.md`, plus the matching file in
   `adapters/` and your project file, if any.

Without installing: `SHEPHERD_CONFIG=path/to/config.toml python3 -m
shepherd COMMAND`.

To try the dashboard on synthetic data:

```
python3 tests/fixtures.py /tmp/shepherd-demo
SHEPHERD_CONFIG=/tmp/shepherd-demo/config.toml python3 -m shepherd.dash
```

## Commands

| Command | Does |
|---|---|
| `shepherd watch [--once]` | poll Gerrit, print and log events |
| `shepherd wait` | block until Gerrit events or dashboard items; exit 0 with JSON, 124 when idle, 2 when another watcher holds the lock |
| `shepherd cq LABEL=BUILD_ID...` | failing tests of try builds; `--output LABEL=BUILD_ID=TEST` prints one test's command and output |
| `shepherd drafts threads\|list\|create` | comment threads, your drafts, new draft replies; never publishes |
| `shepherd dashctl ...` | the agent's side of the dashboard: requests, questions, notes, curation, heartbeat |
| `shepherd dash` | serve the dashboard (also `python3 -m shepherd.dash`) |

Each command has `--help`.

## Configuration

One TOML file; see `config.example.toml` for every key. It is
`$SHEPHERD_CONFIG`, else `$XDG_CONFIG_HOME/shepherd/config.toml` (default
`~/.config/shepherd/config.toml`). Environment variables override single
keys: `SHEPHERD_GERRIT`, `SHEPHERD_PROJECT`, `SHEPHERD_OWNER`,
`SHEPHERD_EXTRA_CLS`, `SHEPHERD_AUTH`, `SHEPHERD_STATE_DIR`,
`SHEPHERD_WATCH_DIR`, `SHEPHERD_DATA_DIR`, `SHEPHERD_PROJECT_FILE`,
`SHEPHERD_DASH_HOST`, `SHEPHERD_DASH_PORT`.

Tracked CLs are the owner's open CLs in the project, `extra_cls`, and every
CL already in the state file; CLs found only through the owner query are
dropped from polling once they close.

## Data

Everything lives in the state directory (`watch_dir` and `data_dir` can
split it, eg to keep using existing files). Every file has one writer,
except the request log, which is append-only.

| File | Writer | Contents |
|---|---|---|
| `cl-watch-state.json` | watcher | Gerrit facts per CL |
| `cl-watch.log` | watcher | event log |
| `cls.json` | agent, via `dashctl curate` | per CL: `bucket`, `next`, `note`, `exp`, `prio`, `basis` |
| `requests.jsonl` | dashboard and agent | requests, questions, notes; see `shepherd/dash/requestlog.py` |
| `agent.json` | agent, via `dashctl` | heartbeat |
| `experiments.json` | optional, `extras/report/seed.py` | experiment table |
| `pinpoint.json` | optional, `extras/pinpoint/pp_sync.py` | latest Pinpoint batch per CL |

The dashboard never talks to Gerrit. It re-reads a file when its mtime or
size changes; the page polls `/version` and swaps in `/board`.

### Buckets and curation

A CL's bucket is derived from Gerrit state (`_derived_bucket` in
`shepherd/dash/store.py`). `cls.json` can override it for open CLs with one
of `todo`, `ping`, `send`, `approved`, `review`, `decide`, `wip`; merged
and abandoned CLs always follow Gerrit. Auto-tracked CLs show under
Untracked until they are curated.

`dashctl curate` stamps each entry with the Gerrit facts it was written
against (`basis`: status, WIP, patchset, unresolved count, Code-Review
votes, last CV result). When any of them changes the entry is stale: its
bucket override stops applying, its text is flagged, and `dashctl stale`
lists it until the agent curates the CL again.

### Order within a bucket

The row to act on next is first (`_priority` in `shepherd/dash/store.py`):
a curated `prio` (lower first), then rows with an open request, then per
bucket:

| Bucket | Order |
|---|---|
| Your move | failed run first, then most open threads, then most recent |
| Ping, Waiting, Keep or abandon | longest quiet first |
| Ready to send, Approved, WIP | best Pinpoint total first, unmeasured last |
| Landed, Abandoned | most recent first |

Clicking a column header sorts by it; the third click returns to this order.

## Extras

Optional, with their own dependencies; the dashboard works without their
data files.

- `extras/pinpoint/pp_sync.py` fills the Pinpoint column. Needs a
  [v8-utils](#dependencies) checkout and a Pinpoint login; runs as
  `uv run --project V8_UTILS python -I extras/pinpoint/pp_sync.py`.
- `extras/report/seed.py --data DATA_PY` writes the experiment table from a
  project data module (`extras/report/data.example.py`); stdlib only.
  `extras/report/gen.py` renders the same data as a PDF report (needs
  reportlab and v8-utils); `pp_rows.py` caches the Pinpoint rows both use.

## Dependencies

The core has none. Extras: reportlab (BSD) for the PDF report; v8-utils
(MIT) for Pinpoint access. Development: ruff (MIT).

## Development

```
python3 -m unittest discover -s tests -t .
uv sync && scripts/install-hooks
```

The hooks check `ruff format`, `ruff check`, license headers
(`scripts/add-license-headers.py`) and checkout-private patterns kept in
`.git/private-hooks/` (see `scripts/check-private-patterns`). Before
publishing, run `tools/check-public.sh`.

## License

MIT; see `LICENSE`. Security notes: `SECURITY.md`.
