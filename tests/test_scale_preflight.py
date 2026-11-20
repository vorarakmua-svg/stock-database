"""The scale pre-flight's tools (build task M5-4): ``qa/scale/synthetic_universe.py``, which clones every company of a
copy under made-up tickers, and ``qa/scale/page_timings.py``, which times the pages and APIs through the TestClient
without writing to the database.

docs/superpowers/specs/2026-10-25-universe-automation-design.md ("Scale")
"""
import hashlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from src.universe.tickers import normalize_ticker, read_tickers_file

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    """A qa/scale script as a module (registered, as its dataclasses need)."""
    spec = importlib.util.spec_from_file_location(f"qa_scale_{name}", ROOT / "qa" / "scale" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


synthetic = _load("synthetic_universe")
timings = _load("page_timings")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rows(conn, table, ticker):
    """A ticker's rows in *table* as dicts, in a stable order."""
    cur = conn.execute(f'SELECT * FROM "{table}" WHERE ticker = ?', (ticker,))
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    return sorted(rows, key=lambda r: [str(v) for v in r.values()])


@pytest.fixture
def source(web_db):
    """The web fixture's four companies (AAA, BBB, CCC, EEE, and the ^GSPC bars) with a Form 4 filing of AAA's and
    its amendment, a trade of each, and a guru's quarter holding AAA and ZZZ (not ours), both CUSIPs mapped."""
    conn = sqlite3.connect(web_db)
    conn.executemany(
        "INSERT INTO insider_filings (accession, owner_cik, ticker, form, filed_date, superseded_by, collected_at) "
        "VALUES (?, ?, 'AAA', ?, ?, ?, '2026-10-01')",
        [("0000000001-26-000001", "0000000009", "4", "2026-03-02", "0000000001-26-000002"),
         ("0000000001-26-000002", "0000000009", "4/A", "2026-03-09", None)])
    conn.executemany(
        "INSERT INTO insider_trades (accession, seq, ticker, kind, date, code, shares, price, superseded_by) "
        "VALUES (?, 0, 'AAA', 'nonderivative', '2026-02-27', 'P', 100, 50.0, ?)",
        [("0000000001-26-000001", "0000000001-26-000002"), ("0000000001-26-000002", None)])
    conn.execute("INSERT INTO guru_quarters (guru_cik, period, total_value, positions, complete, has_prior, "
                 "accessions, filed, computed_at) VALUES ('1067983', '2026-06-30', 1000.0, 2, 1, 0, '[]', "
                 "'2026-08-14', '2026-10-01')")
    conn.executemany(
        "INSERT INTO guru_positions (guru_cik, period, security, put_call, ticker, cusips, issuer, shares, value_usd, "
        "weight) VALUES ('1067983', '2026-06-30', ?, '', ?, ?, ?, 10, 500.0, 0.5)",
        [("AAA", "AAA", "000000AA1", "AAA CORP"), ("ZZZ", "ZZZ", "000000ZZ9", "ZZZ INC")])
    conn.executemany("INSERT INTO cusip_map (cusip, ticker, name, source, mapped_at) VALUES (?, ?, ?, ?, '2026-10-01')",
                     [("000000AA1", "AAA", "AAA Corp", "universe"), ("000000ZZ9", "ZZZ", "ZZZ Inc", "openfigi")])
    conn.commit()
    conn.close()
    return web_db


# ---- synthetic_universe ---------------------------------------------------------------------------------------------


def test_every_company_is_cloned_with_all_its_rows_and_the_source_is_left_alone(source, tmp_path, capsys):
    out = tmp_path / "synthetic.db"
    before = _sha(source)
    assert synthetic.main(["--source", str(source), "--out", str(out), "--copies", "2"]) == 0
    assert _sha(source) == before
    printed = capsys.readouterr().out
    assert "4 companies" in printed and "12 companies (2 clones of each), integrity ok" in printed
    assert "file size:" in printed
    conn = sqlite3.connect(out)
    tickers = {t for (t,) in conn.execute("SELECT ticker FROM companies")}
    assert tickers == {f"{t}{s}" for t in ("AAA", "BBB", "CCC", "EEE") for s in ("", "Q1", "Q2")}
    keyed = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
             if "ticker" in {row[1] for row in conn.execute(f'PRAGMA table_info("{t}")')}]
    assert {"price_bars", "financials_annual", "metrics_annual", "market_snapshots", "insider_trades",
            "insider_filings", "dividend_events", "holders", "collection_runs"} <= set(keyed)
    for table in keyed:
        if table == "cusip_map":
            continue
        for k in (1, 2):
            original = _rows(conn, table, "AAA")
            clone = _rows(conn, table, f"AAAQ{k}")
            assert len(clone) == len(original), table
            expected = []
            for row in original:
                row = {**row, "ticker": f"AAAQ{k}"}
                if table == "companies":
                    row["company_name"] += f" [Q{k}]"
                for column in ("accession", "superseded_by"):
                    if table.startswith("insider_") and row.get(column) is not None:
                        row[column] += f"-Q{k}"
                if table == "guru_positions":
                    row["security"] += f"Q{k}"
                expected.append(row)
            assert clone == sorted(expected, key=lambda r: [str(v) for v in r.values()]), table
    # the benchmark, a guru's position in a company not ours, the CUSIP map and the gurus' quarters: as they were
    assert conn.execute("SELECT COUNT(*) FROM price_bars WHERE ticker LIKE '^GSPC%'").fetchone()[0] == 30
    assert conn.execute("SELECT ticker FROM guru_positions WHERE ticker LIKE 'ZZZ%'").fetchall() == [("ZZZ",)]
    assert conn.execute("SELECT cusip, ticker FROM cusip_map ORDER BY cusip").fetchall() == [
        ("000000AA1", "AAA"), ("000000ZZ9", "ZZZ")]
    assert conn.execute("SELECT COUNT(*) FROM guru_quarters").fetchone()[0] == 1
    # each clone's amendment names its own filing
    assert conn.execute("SELECT superseded_by FROM insider_filings WHERE ticker = 'AAAQ2' AND form = '4'"
                        ).fetchall() == [("0000000001-26-000002-Q2",)]
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_an_existing_copy_is_never_overwritten(source, tmp_path, capsys):
    out = tmp_path / "synthetic.db"
    out.write_bytes(b"mine")
    assert synthetic.main(["--source", str(source), "--out", str(out)]) == 1
    assert out.read_bytes() == b"mine"
    assert "refusing to overwrite" in capsys.readouterr().out


def test_a_clone_that_is_a_ticker_already_stops_the_run_and_removes_the_copy(source, tmp_path, capsys):
    conn = sqlite3.connect(source)
    conn.execute("INSERT INTO cusip_map (cusip, ticker, source, mapped_at) VALUES "
                 "('000000BB2', 'BBBQ2', 'openfigi', '2026-10-01')")
    conn.commit()
    conn.close()
    before = _sha(source)
    out = tmp_path / "synthetic.db"
    assert synthetic.main(["--source", str(source), "--out", str(out), "--copies", "3"]) == 1
    assert "BBBQ2" in capsys.readouterr().out
    assert not out.exists() and not Path(str(out) + "-wal").exists()
    assert _sha(source) == before


def test_a_clone_that_is_a_universe_files_ticker_is_refused(source, tmp_path, monkeypatch, capsys):
    universe = tmp_path / "universe"
    universe.mkdir()
    (universe / "core.txt").write_text("# the core list\nAAA\n", encoding="utf-8")
    (universe / "sp500-2026-10-25.csv").write_text("ticker,name\nCCCQ1,Real Co\n", encoding="utf-8")
    monkeypatch.setattr(synthetic, "UNIVERSE_DIR", universe)
    out = tmp_path / "synthetic.db"
    assert synthetic.main(["--source", str(source), "--out", str(out)]) == 1
    assert "CCCQ1" in capsys.readouterr().out and not out.exists()


def test_clone_tickers_pass_the_apps_ticker_rules_and_take_no_real_ticker():
    universe = ROOT / "universe"
    taken = set(read_tickers_file(universe / "core.txt"))
    for path in universe.glob("sp500-*.csv"):
        taken.update(read_tickers_file(path))
    clones = synthetic.clone_tickers(read_tickers_file(universe / "core.txt"), 3, taken)
    assert clones["BRK-B"] == ["BRK-BQ1", "BRK-BQ2", "BRK-BQ3"]
    every = [c for cs in clones.values() for c in cs]
    assert len(every) == len(set(every)) == 3 * len(clones)
    assert all(normalize_ticker(c) == c for c in every) and not set(every) & taken


def test_a_clone_not_in_yahoos_form_or_already_taken_is_refused():
    with pytest.raises(ValueError, match="Yahoo's form"):
        synthetic.clone_tickers(["BRK.B"], 1, set())
    with pytest.raises(ValueError, match="AAAQ2"):
        synthetic.clone_tickers(["AAA"], 2, {"AAAQ2"})


# ---- page_timings ---------------------------------------------------------------------------------------------------


def test_the_screeners_results_have_the_tighter_gate():
    for path in ("/ui/screen", "/ui/screen?sort=gurus_holding&sort_dir=desc", "/api/screen?industry=28",
                 "/api/export/screen.csv"):
        assert timings.gate_of(path) == timings.SCREEN_GATE_MS == 500
    for path in ("/screener", "/quality", "/api/market", "/ui/stocks/AAA/val", "/api/export/stock/AAA/bars.csv"):
        assert timings.gate_of(path) == timings.PAGE_GATE_MS == 1000


def test_the_default_tickers_are_the_heaviest_stock_pages(source):
    conn = sqlite3.connect(source)
    conn.executemany("INSERT INTO insider_trades (accession, seq, ticker, kind) "
                     "VALUES ('x', ?, 'BBB', 'nonderivative')", [(1,), (2,), (3,)])
    conn.commit()
    # BBB has the most trades (3 to AAA's 2), AAA the most bars (^GSPC is no company) and gurus' positions (ZZZ isn't)
    assert timings.default_tickers(conn) == ["BBB", "AAA"]
    conn.close()


def test_every_path_is_timed_read_only_and_answers(source, capsys):
    before = timings.fingerprint(source)
    assert timings.main(["--db", str(source), "--runs", "1"]) == 0
    assert timings.fingerprint(source) == before
    out = capsys.readouterr().out
    assert "tickers: AAA" in out and "/gurus/1067983" in out and "/ui/stocks/AAA/val-indicators" in out
    assert "over the gate: 0" in out and "5xx responses: 0" in out and "other non-200 responses: 0" in out
    assert "database unchanged (SHA-256 of stock.db, stock.db-wal): yes" in out
    rows = [line.split() for line in out.splitlines() if line.startswith("/")]
    assert len(rows) >= 80 and all(row[1] == "200" for row in rows)


def test_a_write_during_the_run_exits_2(source, monkeypatch, capsys):
    def writing(client, paths, runs):
        conn = sqlite3.connect(source)
        conn.execute("UPDATE companies SET company_name = 'changed' WHERE ticker = 'AAA'")
        conn.commit()
        conn.close()
        return [timings.Timing(path, timings.gate_of(path)) for path in paths]

    monkeypatch.setattr(timings, "measure", writing)
    assert timings.main(["--db", str(source), "--runs", "1", "--tickers", "AAA"]) == 2
    assert "database unchanged (SHA-256 of stock.db, stock.db-wal): NO" in capsys.readouterr().out


def test_a_median_over_its_gate_exits_1_and_is_flagged(source, monkeypatch, capsys):
    monkeypatch.setattr(timings, "PAGE_GATE_MS", -1.0)
    monkeypatch.setattr(timings, "SCREEN_GATE_MS", -1.0)
    assert timings.main(["--db", str(source), "--runs", "1", "--tickers", "AAA"]) == 1
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.startswith("/")]
    assert rows and all(line.endswith("  OVER") for line in rows)
    assert f"over the gate: {len(rows)}" in out


def test_a_changed_database_or_wal_is_caught_and_an_empty_wal_is_none(tmp_path):
    db = tmp_path / "x.db"
    wal = tmp_path / "x.db-wal"
    db.write_bytes(b"one")
    before = timings.fingerprint(db)
    wal.write_bytes(b"")
    assert timings.fingerprint(db) == before == {"x.db": hashlib.sha256(b"one").hexdigest(), "x.db-wal": None}
    wal.write_bytes(b"frame")
    assert timings.fingerprint(db) != before
    wal.unlink()
    db.write_bytes(b"two")
    assert timings.fingerprint(db) != before
