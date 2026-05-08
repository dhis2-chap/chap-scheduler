# Loop safety rule

Copy this block verbatim into `CLAUDE.md`, `AGENTS.md`, cursor rules, or
anywhere else your assistant reads guidelines from.

---

## No unbounded loops

Every `while` loop **must** have at least one of, and almost always both:

- A **deadline** check (`time.monotonic() >= deadline`) that raises a
  named exception (`TimeoutError`, not a bare `RuntimeError`) when the
  budget is spent. The deadline is set **before** the loop starts; do
  not advance it inside the loop.
- A **bounded counter** (`for _ in range(MAX)`, or `while count < MAX`)
  with a clear maximum baked in as a module-level constant. When the
  counter is hit, **raise** -- never silently truncate.

If the loop polls something, the polled response **must not** be able to
return the "still working" state for an input that doesn't exist. If
the upstream API does that (e.g. chap-core returns `200 "PENDING"` for
typo'd job ids -- see `chap_client/CHAP_SPEC_DRIFT.md` finding #7),
the loop **must** validate the input exists *before* entering the
poll cycle and raise `ValueError` synchronously on a miss. Otherwise a
typo eats the entire timeout for nothing.

Reference implementation in this repo:
`chap_client.ChapClient.wait_for_job` (in
`chap_client/src/chap_client/endpoints/predictions.py`) -- membership
check + deadline + transient-status set + `time.sleep` between polls.
Three things, all required.

Bonus rules:

- `time.sleep(poll_interval)` belongs at the **end** of the loop body,
  after the terminal-status check. Sleeping at the top means the
  caller waits even when the answer is already available.
- The polling status set is a `frozenset` checked **case-insensitively**
  (`status.upper() not in _TRANSIENT_SET`). Upstream APIs flip casing
  between releases.
- For period / range walks: the cap is a module-level constant with a
  comment explaining what it covers in human units (`# ~10 years
  monthly`). Bumping it is a deliberate code change, not a runtime
  flag.

When you spot a `while True:` in a review, the burden of proof is on the
author: show the deadline, show the input validation, show the
terminal-state check. If any of those three is missing, the loop is a
hang waiting to happen.
