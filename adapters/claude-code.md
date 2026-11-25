# Claude Code

Notes for running `prompt/shepherd.md` in Claude Code. Nothing here is
required; the prompt works with plain foreground commands.

## Session start

```
Shepherd my CLs per <bundle>/prompt/shepherd.md and
<bundle>/adapters/claude-code.md. Read both and the project file named in
the config, then start the loop.
```

## Background waiter

Run the waiter as a background Bash task (`run_in_background: true`):

```
shepherd wait; echo "exit=$?"
```

Claude Code resumes the session when the task exits, so the session is free
for chat while waiting. Restart it after every exit, silently after exit
124. Stopping the task with TaskStop normally releases the lock at once; if
the next waiter exits 2, the old Python process is orphaned and exits on its
next event. `ps` and `pkill` may be blocked by the sandbox; the user can run
`! pkill -f "shepherd wait"`.

## Helpers

Hand "Changing a CL" work to a subagent with `prompt/task-fix.md` as the
brief. Name the worktree and the latest patchset in the brief; the
subagent runs the guard itself.

## MCP and skills

The bundle's own commands cover everything the loop needs. If the session
has them, these are equivalent:

| Bundle command | Alternative |
|---|---|
| `shepherd drafts threads CL` | v8-utils MCP `gerrit_comments(include_drafts=true)` |
| `shepherd drafts create CL` | v8-utils MCP `gerrit_create_comments` |
| worktree creation | v8-utils MCP `worktree`; use `action=refresh` on a plain `git worktree add`, which has no gclient deps |
| build / test | skills `v8-build`, `v8-run-tests` |
| pre-upload review | skill `v8-self-review` |

## Sandbox

- If the sandbox blocks the default state directory (`~/.local/state`),
  set `[paths] state_dir` to a writable directory.
- Heredocs may be blocked; pass draft JSON with
  `printf '%s' '[...]' | shepherd drafts create CL` or from a file.
- Desktop notifications from the watcher are not delivered from inside
  the sandbox; the dashboard and the waiter output are the channels.
- `uv` may need `--no-managed-python` when its managed Pythons live in a
  blocked directory, and `UV_CACHE_DIR` set to a writable directory.
