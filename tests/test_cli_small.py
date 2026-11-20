"""stock-web's --help (BUG-046) and diagnose.py's share counts (BUG-166)."""
import pytest


def test_stock_web_help_prints_usage_and_starts_nothing(monkeypatch, capsys):
    # BUG-046: `stock-web --help` started the server.
    import uvicorn

    from src.webapp import __main__ as web_main
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(a))
    with pytest.raises(SystemExit) as exit_info:
        web_main.main(["--help"])
    assert exit_info.value.code == 0
    assert started == []
    out = capsys.readouterr().out
    assert "usage:" in out and "STOCK_WEB_DB_PATH" in out


def test_stock_web_rejects_an_unknown_argument_without_starting(monkeypatch, capsys):
    import uvicorn

    from src.webapp import __main__ as web_main
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(a))
    with pytest.raises(SystemExit) as exit_info:
        web_main.main(["--port", "8000"])
    assert exit_info.value.code == 2
    assert started == []


def test_diagnose_prints_share_counts_as_counts(capsys):
    # BUG-166: "shares_outstanding: $14.77B".
    import diagnose
    diagnose.show_available_fields({"financials_annual": {2025: {
        "shares_outstanding": 14.77e9, "revenue": 391.0e9, "fiscal_year": 2025}}})
    out = capsys.readouterr().out
    assert "shares_outstanding: 14.77B" in out
    assert "revenue: $391.00B" in out
