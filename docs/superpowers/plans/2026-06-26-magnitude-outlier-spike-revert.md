# Magnitude-Outlier Spike-and-Revert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `magnitude_outlier` flag only a transient spike-and-revert (the real error signature) so legitimate M&A step-changes (which jump and persist) are no longer false-flagged.

**Architecture:** Rewrite `check_field_outliers` in `src/validation/integrity.py` to compare each year against its two chronological neighbors instead of an all-history median; flag only when the value is ≥100× both. Drop the now-unused `statistics` import. One README row + a re-run of the 5-stock live smoke.

**Tech Stack:** Python 3.9+, pytest, ruff, mypy.

## Global Constraints

- Python floor **3.9** — no `X | Y` unions, no `match`.
- ruff line-length **120**, select `E, F, W, I`, imports at top. The rewrite removes the only use of `statistics`, so **delete `import statistics`** or ruff F401 fails.
- mypy clean; `integrity.py` is covered by the existing `"src/validation"` mypy directory entry — do not touch pyproject.
- `_OUTLIER_FACTOR` stays `100.0`, `_MATERIALITY` stays `1_000_000.0`. Flag-only (no mutation). HIGH severity, code `magnitude_outlier`.
- All existing tests stay green; clean filings keep score 100.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `fix/magnitude-outlier-spike-revert` is already checked out.

---

### Task 1: Rewrite `check_field_outliers` to spike-and-revert

**Files:**
- Modify: `src/validation/integrity.py` (`check_field_outliers` + remove `import statistics`)
- Test: `tests/test_integrity.py` (rewrite one test, add one)

**Interfaces:**
- Produces (unchanged signature): `check_field_outliers(annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]) -> List[Finding]` — now flags a scored, interior-year USD value that is ≥100× both chronological neighbors.

- [ ] **Step 1: Rewrite the "fires" test and add the persistent-step-change test**

In `tests/test_integrity.py`, REPLACE the existing `test_outlier_fires_on_1000x_spike` (its spike is in the latest year 2024, which the new logic intentionally never flags) with this version, and ADD the second test right after it:

```python
def test_outlier_fires_on_1000x_spike():
    # Revenue spikes in 2022 and reverts in 2023 -> a one-off anomaly (interior year).
    annual = {
        "2021": _yr(1.0e9, 2.0e9),
        "2022": _yr(1.2e12, 2.1e9),
        "2023": _yr(1.1e9, 2.2e9),
        "2024": _yr(1.2e9, 2.3e9),
    }
    findings = check_field_outliers(annual, {"2021", "2022", "2023", "2024"})
    codes = [(f.code, f.period) for f in findings]
    assert ("magnitude_outlier", "2022") in codes
    assert all(f.severity == "high" for f in findings)


def test_outlier_silent_on_persistent_step_change():
    # Goodwill stays tiny for years, then jumps on an acquisition and PERSISTS.
    # With many tiny pre-merger years the all-history median is tiny, so the OLD
    # median check flagged every post-merger year (the Realty Income / VEREIT false
    # positive). Spike-and-revert must NOT flag a jump that persists (~1x the next).
    tiny = {"goodwill": 1.5e7}
    big = {"goodwill": 3.7e9}
    annual = {
        "2017": tiny, "2018": tiny, "2019": tiny, "2020": tiny, "2021": tiny,
        "2022": big, "2023": big, "2024": big,
    }
    assert check_field_outliers(annual, {"2022", "2023", "2024"}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_integrity.py -k "outlier" -v`
Expected: FAIL — `test_outlier_silent_on_persistent_step_change` fails: under the current all-history-median logic, the five tiny pre-merger years pull the median to ~$1.5e7, so each post-merger year (3.7e9 ≈ 247× the median) is flagged, and the test's `== []` assertion fails. (The rewritten `test_outlier_fires_on_1000x_spike` passes under both old and new logic — it is a regression guard that a true spike still fires.)

- [ ] **Step 3: Rewrite the function and drop the `statistics` import**

In `src/validation/integrity.py`, remove the top-of-file line:

```python
import statistics
```

(Leave `from typing import Any, Dict, Iterable, List, Tuple` — `Tuple` is still used.)

Replace the entire `check_field_outliers` function with:

```python
def check_field_outliers(
    annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag a USD field that spikes >=100x BOTH adjacent years (a one-off anomaly).

    A transient spike-and-revert is the signature of a filing or mis-resolved-tag
    error; a genuine step-change (e.g. goodwill from an acquisition) jumps and
    PERSISTS, staying ~1x the next year, so it is correctly not flagged. The first
    and last points of a field's series -- including the latest year -- are never
    flagged: there is no later value to confirm a reversion, and a just-happened
    jump is indistinguishable from a real recent event.
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    for key in _USD_FIELDS:
        points: List[Tuple[str, float, float]] = []  # (year, signed_value, magnitude)
        for year, period in annual.items():
            v = _num(period, key)
            if v is not None and v != 0:
                points.append((year, v, abs(v)))
        if len(points) < 3:
            continue
        points.sort(key=lambda p: p[0])  # chronological (4-digit year strings)
        for i in range(1, len(points) - 1):
            year, value, mag = points[i]
            if year not in scored or mag < _MATERIALITY:
                continue
            prev_mag = points[i - 1][2]
            next_mag = points[i + 1][2]
            if mag >= _OUTLIER_FACTOR * prev_mag and mag >= _OUTLIER_FACTOR * next_mag:
                findings.append(Finding(
                    HIGH, "magnitude_outlier",
                    f"'{key}' = {value:,.0f} spikes to {mag / prev_mag:.0f}x the prior "
                    f"year and {mag / next_mag:.0f}x the next; likely a one-off filing "
                    f"or tag error.",
                    year,
                ))
    return findings
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `python -m pytest tests/test_integrity.py -k "outlier" -v`
Expected: PASS — `test_outlier_fires_on_1000x_spike` (2022 flagged), `test_outlier_silent_on_persistent_step_change` (no findings), and the pre-existing `test_outlier_silent_on_real_growth_and_small_series`, `test_outlier_only_flags_scored_years`, and `test_magnitude_outlier_excludes_volatile_cashflow_residuals` all green.

- [ ] **Step 5: Run the full suite + linters**

Run: `python -m pytest -q`
Expected: PASS (full suite unchanged in count — one test rewritten, one added).

Run: `python -m ruff check src/validation/integrity.py tests/test_integrity.py && python -m mypy`
Expected: no errors (in particular, no F401 for `statistics`).

- [ ] **Step 6: Commit**

```bash
git add src/validation/integrity.py tests/test_integrity.py
git commit -m "fix: magnitude_outlier flags spike-and-revert, not persistent step-changes" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the magnitude-outlier row**

In `README.md`, in the "### Integrity checks (data-quality score)" table, replace the row:

```markdown
| Magnitude outlier | a USD field wildly inconsistent with its own history (mis-resolved tag / filing error) | ≥ 100× the field's median | −25 |
```

with:

```markdown
| Magnitude outlier | a USD field that spikes then reverts to its prior level — a one-off filing/tag error (persistent step-changes like M&A goodwill are not flagged) | spike ≥ 100× both adjacent years | −25 |
```

- [ ] **Step 2: Verify the full suite and linters**

Run: `python -m pytest -q`
Expected: PASS (full suite).

Run: `python -m ruff check . && python -m mypy`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: magnitude_outlier is now spike-and-revert based" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks) — the merge gate

- [ ] Full suite green: `python -m pytest -q`
- [ ] Lint + types clean: `python -m ruff check . && python -m mypy`
- [ ] **Live smoke (the reason for the fix):** run
  `python -m src.main WMT BAC PGR O XOM --no-yahoo --formats sqlite --db <scratch>/sr.db --output-dir <scratch>/sr --workers 5`,
  then inspect `collection_runs.quality_score` and the data-quality findings per ticker.
  Expected: **O recovers to score 100 with zero `magnitude_outlier` findings**; WMT/BAC/PGR
  unchanged at 100; XOM unchanged at 50 (its energy-coverage issue is separate, not addressed
  here). No `magnitude_outlier` finding on any of the five.
- [ ] Only after the smoke is clean: merge to `main` + clean up the branch.
