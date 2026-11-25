# Security

## Model

- The dashboard is a local, single-user page **without authentication**. It
  binds to `127.0.0.1` by default; keep `[dash] host` on a loopback address.
  Anyone who can reach the port can read the board and approve requests.
- Mutating requests need a custom `X-Dash` header, which a cross-origin page
  can't send without a CORS preflight the server never answers. Requests
  whose `Host` header isn't a loopback name or the configured host are
  rejected, which blocks DNS-rebinding reads.
- Gerrit, Buildbucket and ResultDB reads are anonymous. `shepherd drafts`
  reads the user's existing Gerrit credentials (git-credential-luci or the
  git cookie file) at run time and never stores, logs or prints them.
- Nothing in the bundle publishes Gerrit comments, votes or uploads; the
  agent prompt forbids publishing, and `shepherd drafts` only writes drafts.

## Reporting a vulnerability

Report suspected vulnerabilities privately to the maintainers rather than in
a public issue, with steps to reproduce and the affected commit.
