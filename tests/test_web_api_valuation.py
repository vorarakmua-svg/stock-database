"""/api/stocks/{ticker}/valuation: stored models + live verdict."""
import json
import sqlite3


def _seed_valuations(db_path, ticker="AAA"):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS valuations (
            ticker TEXT NOT NULL, model TEXT NOT NULL,
            applicable INTEGER NOT NULL, na_reason TEXT,
            value_bear REAL, value_base REAL, value_bull REAL,
            assumptions TEXT, basis_fiscal_year INTEGER,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (ticker, model)
        );
        CREATE TABLE IF NOT EXISTS valuation_summary (
            ticker TEXT PRIMARY KEY, n_applicable INTEGER NOT NULL,
            median_bear REAL, median_base REAL, median_bull REAL,
            computed_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'dcf', 1, NULL, 80.0, 100.0, "
        "120.0, ?, 2023, '2024-01-05T00:00:00')",
        (ticker, json.dumps({"growth_base": 0.05, "discount_base": 0.09})),
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES (?, 'ddm', 0, 'no dividend history', "
        "NULL, NULL, NULL, '{}', NULL, '2024-01-05T00:00:00')",
        (ticker,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO valuation_summary VALUES (?, 1, 80.0, 100.0, 120.0, "
        "'2024-01-05T00:00:00')",
        (ticker,),
    )
    conn.commit()
    conn.close()


def test_valuation_endpoint_returns_models_and_verdict(client, web_db):
    _seed_valuations(web_db)
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAA"
    assert len(body["models"]) == 2
    dcf = next(m for m in body["models"] if m["model"] == "dcf")
    assert dcf["applicable"] is True
    assert dcf["assumptions"]["growth_base"] == 0.05  # JSON parsed to dict
    ddm = next(m for m in body["models"] if m["model"] == "ddm")
    assert ddm["na_reason"] == "no dividend history"
    # web_db's AAA snapshot has a current_price; verdict must be derivable
    assert body["verdict"] in ("cheap", "fair", "expensive")
    assert body["verdict_label"]
    assert body["summary"]["median_base"] == 100.0


def test_valuation_endpoint_no_rows_yet(client):
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"] == []
    assert body["verdict"] is None
    assert body["verdict_label"] == "Not valued"
    assert body["summary"] is None


def test_valuation_endpoint_unknown_ticker_404(client):
    resp = client.get("/api/stocks/ZZZ/valuation")
    assert resp.status_code == 404


def test_valuation_endpoint_returns_owner_earnings_verdict(client, web_db):
    import json as _json
    import sqlite3 as _sqlite3
    _seed_valuations(web_db)
    conn = _sqlite3.connect(str(web_db))
    conn.execute(
        "INSERT OR REPLACE INTO valuations VALUES ('AAA', 'owner_earnings', 1, NULL, "
        "200.0, 250.0, 300.0, ?, 2025, '2024-01-05T00:00:00')",
        (_json.dumps({"buy_below": 175.0, "discount_base": 0.07,
                      "beta_used": False}),),
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    body = resp.json()
    # AAA's seeded price is 105.0, well below buy_below 175 -> cheap on this method,
    # even though the five-model median says otherwise. That divergence is the point.
    assert body["owner_earnings_verdict"] == "cheap"
    assert body["owner_earnings_verdict_label"] == "Looks cheap"
    oe = next(m for m in body["models"] if m["model"] == "owner_earnings")
    assert oe["assumptions"]["beta_used"] is False


def test_valuation_endpoint_owner_earnings_absent(client):
    resp = client.get("/api/stocks/AAA/valuation")
    assert resp.status_code == 200
    assert resp.json()["owner_earnings_verdict"] is None
