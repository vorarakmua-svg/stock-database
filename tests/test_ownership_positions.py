"""The gurus' quarters materialized when collecting (Milestone 3, M3-6): ``guru_quarters`` and ``guru_positions``, each
guru's rows replaced in one transaction from its ``load_guru`` quarters: one row per position held (put/call '' for
shares and principal amounts) with its change from the quarter before, and a 'sold_out' row for each position of the
quarter before that's gone. ``--gurus`` stores them after mapping the CUSIPs; ``--positions`` rebuilds them offline
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import json
import sqlite3
from decimal import Decimal

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.ownership import collect
from src.ownership.collect import FilingRef, store_guru_positions
from src.ownership.gurus import GURUS, Guru
from src.ownership.portfolio import load_guru
from src.ownership.thirteenf import Holding, ThirteenF

Q1, Q2 = "2026-03-31", "2026-06-30"
BUFFETT = Guru("1067983", "BERKSHIRE HATHAWAY INC", "Warren Buffett")
ACKMAN = Guru("2026053", "PERSHING SQUARE INC.", "Bill Ackman",
              former=(("1336528", "Pershing Square Capital Management, L.P."),))
APPLE, COKE, BOOKING, GONE, LATE = "037833100", "191216100", "09857L108", "999999999", "888888888"
NAMES = {APPLE: "APPLE INC", COKE: "COCA COLA CO", BOOKING: "BOOKING HOLDINGS INC", GONE: "GONE CORP",
         LATE: "LATE CO"}
NOW = "2026-10-22T00:00:00+00:00"
QUARTER_COLUMNS = "guru_cik, period, total_value, positions, complete, has_prior, accessions, filed"
POSITION_COLUMNS = ("security, put_call, ticker, cusips, issuer, title_of_class, share_type, shares, value_usd, "
                    "weight, action, shares_before, value_before, split_factor, change_pct")


def _filing(accession, filer, period, filed, holdings, form="13F-HR", amendment=None, entry_total=None):
    """A stored-to-be 13F: its feed reference and the parsed filing, *holdings* (cusip, shares, dollars[, put_call]),
    its cover's totals agreeing with them unless *entry_total* says otherwise."""
    rows = [Holding(row=i, cusip=h[0], issuer=NAMES[h[0]], title_of_class="COM", value=Decimal(h[2]),
                    value_usd=float(h[2]), shares=float(h[1]), share_type="SH", put_call=h[3] if len(h) > 3 else None)
            for i, h in enumerate(holdings)]
    parsed = ThirteenF(accession=accession, form=form, filed=filed, period=period, amendment_type=amendment,
                       entry_total=len(rows) if entry_total is None else entry_total,
                       value_total=sum((h.value for h in rows), Decimal(0)), holdings=rows)
    return FilingRef(accession, form, filed, period, f"{accession}.txt", filer), parsed


def _berkshire():
    """Berkshire's Q1 and Q2 2026: in Q2 it adds Apple, buys Apple calls and Booking, sells out of Coca-Cola (whose
    2-for-1 split falls between) and an unmapped holding, and a NEW HOLDINGS amendment filed later adds one more."""
    return [
        _filing("B-Q1", "1067983", Q1, "2026-05-15", [(APPLE, 100, 20000), (COKE, 50, 3000), (GONE, 10, 1000)]),
        _filing("B-Q2", "1067983", Q2, "2026-08-14", [(APPLE, 150, 33000), (APPLE, 10, 2000, "Call"),
                                                      (BOOKING, 5, 25000)]),
        _filing("B-Q2-A", "1067983", Q2, "2026-09-02", [(LATE, 1, 100)], form="13F-HR/A", amendment="NEW HOLDINGS"),
    ]


def _pershing():
    """Pershing Square's Q2 2026: Pershing Square Inc.'s report, and the other filer's, which fails its checksum."""
    return [
        _filing("P-Q2", "2026053", Q2, "2026-08-14", [(BOOKING, 10, 50000)]),
        _filing("P-Q2-X", "1336528", Q2, "2026-08-14", [(APPLE, 1, 1)], entry_total=9),
    ]


def _seed(tmp_path):
    db = tmp_path / "gurus.db"
    store = SQLiteStore(db_path=db)
    store.export_guru_filings(BUFFETT, _berkshire(), NOW)
    store.export_guru_filings(ACKMAN, _pershing(), NOW)
    store.export_cusip_map([{"cusip": c, "ticker": t, "name": NAMES[c], "source": "universe", "mapped_at": NOW}
                            for c, t in ((APPLE, "AAPL"), (COKE, "KO"), (BOOKING, "BKNG"))]
                           + [{"cusip": GONE, "ticker": None, "name": NAMES[GONE], "source": "none", "mapped_at": NOW}])
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('KO', '2026-04-15', 2.0)")
    conn.commit()
    conn.close()
    return db


def _rows(db, sql, *params):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _positions(db, cik, period):
    return _rows(db, f"SELECT {POSITION_COLUMNS} FROM guru_positions WHERE guru_cik = ? AND period = ? "
                     "ORDER BY security, put_call", cik, period)


def _quarters(db):
    return _rows(db, f"SELECT {QUARTER_COLUMNS} FROM guru_quarters ORDER BY guru_cik, period")


# ---- the tables ---------------------------------------------------------------------------------------------------


def test_the_quarters_and_positions_tables(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "gurus.db")
    conn = store._connect()
    store._create_schema(conn)
    info = {table: [(r[1], r[2], r[3], r[5]) for r in conn.execute(f"PRAGMA table_info({table})")]
            for table in ("guru_quarters", "guru_positions")}
    indexes = {name: [r[2] for r in conn.execute(f"PRAGMA index_info({name})")]
               for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' "
                                           "AND tbl_name = 'guru_positions' AND sql IS NOT NULL")}
    conn.close()
    # (name, type, NOT NULL, place in the primary key)
    assert info["guru_quarters"] == [
        ("guru_cik", "TEXT", 1, 1), ("period", "TEXT", 1, 2), ("total_value", "REAL", 0, 0),
        ("positions", "INTEGER", 0, 0), ("complete", "INTEGER", 0, 0), ("has_prior", "INTEGER", 0, 0),
        ("accessions", "TEXT", 0, 0), ("filed", "TEXT", 0, 0), ("computed_at", "TEXT", 1, 0)]
    assert info["guru_positions"] == [
        ("guru_cik", "TEXT", 1, 1), ("period", "TEXT", 1, 2), ("security", "TEXT", 0, 3), ("put_call", "TEXT", 1, 4),
        ("ticker", "TEXT", 0, 0), ("cusips", "TEXT", 0, 0), ("issuer", "TEXT", 0, 0), ("title_of_class", "TEXT", 0, 0),
        ("share_type", "TEXT", 0, 0), ("shares", "REAL", 0, 0), ("value_usd", "REAL", 0, 0), ("weight", "REAL", 0, 0),
        ("action", "TEXT", 0, 0), ("shares_before", "REAL", 0, 0), ("value_before", "REAL", 0, 0),
        ("split_factor", "REAL", 0, 0), ("change_pct", "REAL", 0, 0)]
    assert indexes == {"idx_guru_positions_ticker": ["ticker"], "idx_guru_positions_guru": ["guru_cik", "period"]}


# ---- the store method ---------------------------------------------------------------------------------------------


def test_each_quarter_is_stored_with_its_filings(tmp_path):
    db = _seed(tmp_path)
    store_guru_positions(db, [BUFFETT, ACKMAN])
    assert _quarters(db) == [
        # Berkshire's Q1: no Q4 2025 stored, so no changes.
        ("1067983", Q1, 24000.0, 3, 1, 0, '["B-Q1"]', "2026-05-15"),
        # Q2: the amendment's row added; filed when the amendment was, the latest of its two filings.
        ("1067983", Q2, 60100.0, 4, 1, 1, '["B-Q2", "B-Q2-A"]', "2026-09-02"),
        # Pershing Square: the other filer's report failed its checksum, so the quarter is incomplete.
        ("2026053", Q2, 50000.0, 1, 0, 0, '["P-Q2"]', "2026-08-14")]
    [(computed_at,)] = _rows(db, "SELECT DISTINCT computed_at FROM guru_quarters")
    assert computed_at.endswith("+00:00")  # the run's time, in UTC


def test_each_position_held_with_its_change_from_the_quarter_before(tmp_path):
    db = _seed(tmp_path)
    store_guru_positions(db, [BUFFETT])
    held = [row for row in _positions(db, "1067983", Q2) if row[10] != "sold_out"]
    assert held == [
        ("888888888", "", None, "888888888", "LATE CO", "COM", "SH", 1.0, 100.0, pytest.approx(100 / 60100), "new",
         None, None, 1.0, None),
        ("AAPL", "", "AAPL", APPLE, "APPLE INC", "COM", "SH", 150.0, 33000.0, pytest.approx(33000 / 60100), "added",
         100.0, 20000.0, 1.0, 0.5),
        # The calls apart from the shares, under their put/call.
        ("AAPL", "Call", "AAPL", APPLE, "APPLE INC", "COM", "SH", 10.0, 2000.0, pytest.approx(2000 / 60100), "new",
         None, None, 1.0, None),
        ("BKNG", "", "BKNG", BOOKING, "BOOKING HOLDINGS INC", "COM", "SH", 5.0, 25000.0,
         pytest.approx(25000 / 60100), "new", None, None, 1.0, None)]


def test_a_position_gone_since_the_quarter_before_is_a_sold_out_row(tmp_path):
    db = _seed(tmp_path)
    store_guru_positions(db, [BUFFETT])
    gone = [row for row in _positions(db, "1067983", Q2) if row[10] == "sold_out"]
    assert gone == [
        # No shares, value or weight: what was held before, as filed, and the split between (Coca-Cola's 2-for-1).
        ("999999999", "", None, GONE, "GONE CORP", "COM", "SH", None, None, None, "sold_out", 10.0, 1000.0, 1.0,
         None),
        ("KO", "", "KO", COKE, "COCA COLA CO", "COM", "SH", None, None, None, "sold_out", 50.0, 3000.0, 2.0, None)]


def test_without_the_quarter_before_the_positions_have_no_change(tmp_path):
    db = _seed(tmp_path)
    store_guru_positions(db, [BUFFETT])
    assert _positions(db, "1067983", Q1) == [
        ("999999999", "", None, GONE, "GONE CORP", "COM", "SH", 10.0, 1000.0, pytest.approx(1000 / 24000), None,
         None, None, None, None),
        ("AAPL", "", "AAPL", APPLE, "APPLE INC", "COM", "SH", 100.0, 20000.0, pytest.approx(20000 / 24000), None,
         None, None, None, None),
        ("KO", "", "KO", COKE, "COCA COLA CO", "COM", "SH", 50.0, 3000.0, pytest.approx(3000 / 24000), None, None,
         None, None, None)]


def test_a_rerun_replaces_the_gurus_rows_and_leaves_the_others(tmp_path):
    db = _seed(tmp_path)
    store_guru_positions(db, [BUFFETT, ACKMAN])
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM guru_holdings WHERE accession = 'B-Q2-A'")
    conn.execute("DELETE FROM guru_filings WHERE accession = 'B-Q2-A'")
    conn.execute("UPDATE guru_holdings SET shares = 99 WHERE accession = 'P-Q2'")  # not rebuilt below
    conn.commit()
    conn.close()
    store_guru_positions(db, [BUFFETT])
    assert _quarters(db)[1] == ("1067983", Q2, 60000.0, 3, 1, 1, '["B-Q2"]', "2026-08-14")
    assert "888888888" not in [row[0] for row in _positions(db, "1067983", Q2)]
    assert _positions(db, "2026053", Q2)[0][7] == 10.0  # Pershing Square's rows as stored before
    assert _rows(db, "SELECT COUNT(*) FROM guru_positions WHERE guru_cik = '1067983'") == [(8,)]  # 3 + 3 and 2 gone


def test_a_guru_without_quarters_any_more_has_none_stored(tmp_path):
    db = _seed(tmp_path)
    store_guru_positions(db, [BUFFETT])
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM guru_holdings WHERE guru_cik = '1067983'")
    conn.execute("DELETE FROM guru_filings WHERE guru_cik = '1067983'")
    conn.commit()
    conn.close()
    assert store_guru_positions(db, [BUFFETT]) == {"1067983": {"quarters": 0, "positions": 0}}
    assert _rows(db, "SELECT COUNT(*) FROM guru_quarters") == _rows(db, "SELECT COUNT(*) FROM guru_positions") == [
        (0,)]


def test_a_gurus_rows_are_replaced_in_one_transaction(tmp_path):
    db = _seed(tmp_path)
    store_guru_positions(db, [BUFFETT])
    before = (_quarters(db), _positions(db, "1067983", Q1), _positions(db, "1067983", Q2))
    conn = sqlite3.connect(db)
    try:
        loaded = load_guru(conn, "1067983")
    finally:
        conn.close()
    with pytest.raises(sqlite3.IntegrityError):  # a quarter without the time it was computed: nothing of it written
        SQLiteStore(db_path=db).export_guru_positions(loaded, None)
    assert (_quarters(db), _positions(db, "1067983", Q1), _positions(db, "1067983", Q2)) == before  # nor deleted


# ---- store_guru_positions -----------------------------------------------------------------------------------------


def test_the_stored_quarters_are_the_loaders(tmp_path):
    db = _seed(tmp_path)
    lines = []
    summary = store_guru_positions(db, [BUFFETT, ACKMAN], log=lines.append)
    assert summary == {"1067983": {"quarters": 2, "positions": 9}, "2026053": {"quarters": 1, "positions": 1}}
    assert len(lines) == 2 and all(line.isascii() for line in lines) and "Warren Buffett (1067983)" in lines[0]
    conn = sqlite3.connect(db)
    try:
        loaded = load_guru(conn, "1067983")
    finally:
        conn.close()
    for period, quarter in loaded.quarters.items():
        stored = {(s, p or None): (shares, value, action) for s, p, _, _, _, _, _, shares, value, _, action, *_ in
                  _positions(db, "1067983", period) if action != "sold_out"}
        assert stored == {key: (p.shares, p.value_usd, next((c.action for c in loaded.changes[period] or ()
                                                              if (c.security, c.put_call) == key), None))
                          for key, p in quarter.positions.items()}
        [(total, accessions)] = _rows(db, "SELECT total_value, accessions FROM guru_quarters WHERE guru_cik = ? "
                                          "AND period = ?", "1067983", period)
        assert (total, json.loads(accessions)) == (quarter.total_value, list(quarter.accessions))


def test_store_guru_positions_on_a_database_without_the_gurus_filings(tmp_path):
    assert store_guru_positions(tmp_path / "fresh.db", [BUFFETT]) == {"1067983": {"quarters": 0, "positions": 0}}
    assert _rows(tmp_path / "fresh.db", "SELECT COUNT(*) FROM guru_quarters") == [(0,)]


# ---- the CLI ------------------------------------------------------------------------------------------------------


def _no_collection(monkeypatch):
    """Make every network step of the CLI fail the test: the SEC handler, its sources, OpenFIGI and the collectors."""
    import src.fetchers.sec_handler as sec_handler

    def refuse(*args, **kwargs):
        raise AssertionError("the offline rebuild asked the network")

    for module, name in ((sec_handler, "SECHandler"), (collect, "ThirteenFSource"), (collect, "Form4Source"),
                         (collect, "OpenFIGI"), (collect, "collect_gurus"), (collect, "collect_insiders"),
                         (collect, "map_cusips")):
        monkeypatch.setattr(module, name, refuse)


def test_the_cli_rebuilds_the_positions_offline(tmp_path, monkeypatch, capsys):
    db = _seed(tmp_path)
    _no_collection(monkeypatch)
    assert collect.main(["--positions", "--db", str(db), "--cache-dir", str(tmp_path / "cache")]) == 0
    assert [row[:2] for row in _quarters(db)] == [("1067983", Q1), ("1067983", Q2), ("2026053", Q2)]  # of GURUS
    out = capsys.readouterr().out
    assert out.isascii() and f"Guru quarters stored: 3 quarters, 10 position rows for {len(GURUS)} gurus" in out


def test_the_gurus_run_stores_the_positions_after_mapping(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(collect, "collect_gurus", lambda db, gurus, quarters, source, **kw: calls.append(
        "gurus") or {})
    monkeypatch.setattr(collect, "map_cusips", lambda db, client, **kw: calls.append("map") or {
        "universe": 0, "openfigi": 0, "none": 0, "errors": []})
    monkeypatch.setattr(collect, "store_guru_positions", lambda db, gurus, **kw: calls.append(
        ("positions", db, tuple(gurus))) or {})
    db = tmp_path / "x.db"
    args = ["--gurus", "--db", str(db), "--cache-dir", str(tmp_path / "cache")]
    assert collect.main(args) == 0
    assert calls == ["gurus", "map", ("positions", db, GURUS)]
    calls.clear()
    collect.main(args + ["--no-openfigi"])
    assert calls == ["gurus", "map", ("positions", db, GURUS)]
    calls.clear()
    collect.main(args + ["--positions"])  # once, after the mapping
    assert calls == ["gurus", "map", ("positions", db, GURUS)]
    assert "Guru quarters stored: 0 quarters, 0 position rows for 0 gurus" in capsys.readouterr().out
