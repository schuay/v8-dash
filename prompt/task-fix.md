# Task: change a CL

A brief for a helper agent, or a checklist when you do it yourself. Fill in
the fields in angle brackets.

CL: <number> "<title>", latest patchset PS<n>, worktree <path>.

What to change: <the failure and its cause, or the review comments with
their thread ids>.

1. In the worktree, check that `git status` is clean and that HEAD's tree
   matches PS<n> (`git cl status`, or compare `git rev-parse HEAD^{tree}`
   with the tree of the uploaded patchset). If either fails, stop and report;
   don't reset, stash or check out anything.
2. Make the change. Keep it to what is asked.
3. Build and run the tests named in the project file; for a test failure,
   also run the failing test on the configuration it failed on.
4. Commit as a new commit (never amend), `git cl upload`, start a dry run.
5. For review comments: write one draft reply per thread with
   `shepherd drafts create <CL>`, eg
   `[{"in_reply_to": "<thread id>", "message": "Done", "unresolved": false}]`.
   Never publish.
6. Report: the new patchset, what changed, test results, and anything left
   undone or uncertain.
