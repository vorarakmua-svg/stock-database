# Loop addendum (2026-11-02)

Read with `qa/LOOP_PROMPT.md`. Where the two differ, this file wins.

## Mandate
The user said "loop all fix bugs, use your recommendation for questions, loop until all bug fixed". So:
- Every item in `qa/BUGS.md` is worked until it is VERIFIED, or WONTFIX with a reason a reviewer would accept.
- The former NEEDS-USER items carry a `Decision:` line. Implement exactly that decision, test-first, and measure
  what it moves (medians, verdicts, owner-earnings verdicts, historical-value labels) in the LOG entry.
- A question that arises in the loop takes the loop's recommendation: record it with its options and a
  `Decision:` line, then implement it. Exceptions, which stay as documented defaults and never get done by the loop:
  writing to the user's real DB, installing software or scheduled tasks, bulk downloads, deleting user files,
  anything outside the repo and `SCRATCH`.
- Valuation policy changes are allowed only where a `Decision:` line names them.

## How the work runs
- **Orchestrator / worker** (CLAUDE.md): workers prepare each fix in a `git archive` export under `SCRATCH\<bug>`
  and never edit the checkout. The orchestrator reviews the diff, proves RED on an export of HEAD plus the new
  tests, applies the diff with `git apply --ignore-whitespace`, restores CRLF, re-runs the mutants
  (`%LOCALAPPDATA%\stockdb-qa\tools\mutants.py <spec> --root <tree>`) and the gates, then records, commits and
  pushes.
- **Units**: one P0-P2 fix, or up to 3 related P3s. Priority: P1, then P2, oldest first; then P3s by area
  (VALID, VAL, STORE, PARSE, GATHER, then the rest).
- **Memory**: the machine has 15 GB, often under 5 GB free. Check free RAM before each heavy job (DB copies,
  all-filer census, the engine over 502, the full suite) and run it only at 4 GB or more; never under 2 GB; one heavy
  job at a time. If memory stays low, do light work (reading, tests on one company, docs) and log the wait.
- **Commits**: end messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01HtCgyNNBLCL6t9xpGH4zAg`.
  Commit with `-F msgfile` in one `&&` chain after the gates.
- **CI** (Q10): open the draft PR once, with `gh pr create --draft --base main`, title "QA loop: full-spectrum
  testing fixes"; its body ends with the session's attribution lines. If `gh` isn't authenticated, log it as
  BLOCKED and go on with the local gates.
- **Review cadence**: an opus code review after every 5 fixes, as before (review 23 was the last).

## Done
Section 9 of `qa/LOOP_PROMPT.md` still defines Done, with "NEEDS-USER" read as "DECIDED". Print
`<promise>QA LOOP COMPLETE</promise>` only when it is met and verified in that iteration.
