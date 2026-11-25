# Project rules: example performance work

Copy this file, adapt it, and point `[paths] project_file` in the config at
the copy. The agent reads it after `prompt/shepherd.md`; its rules extend
and override the generic ones.

## Context

The CLs come from a batch of performance experiments. Each experiment has
an ID (`exp-NN`) and is listed in `data.py` (see
`extras/report/data.example.py`); `extras/report/seed.py --data data.py`
turns it into the dashboard's experiment table. A CL's experiment is its
label: name CLs as 100001 "[parser] Cache scope lookups" (exp-01).

## Build and test

- Build: `tools/dev/gm.py arm64.optdebug` (add the configuration a failure
  happened on, eg a build without pointer compression).
- Test: `tools/run-tests.py --outdir out/arm64.optdebug <suite>`.

## Ownership

- The user handles the review comments on 100005 themselves.
- CLs split out of an experiment keep its ID; curate them with
  `--exp exp-NN`.

## Benchmarks

- Pinpoint results: after starting jobs or when results land, refresh the
  dashboard column with `extras/pinpoint/pp_sync.py`.
- Quote a JetStream Total as the geomean of the significant story ratios
  (non-significant stories count as unchanged), not Pinpoint's Total row,
  which is often not significant.

## Lessons

- A build without pointer compression has 32-bit Smis. Tests that assert
  deopts for values outside the 31-bit Smi range fail there.
