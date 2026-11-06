"""Configuration makes no folders; writers make their own (BUG-284, BUG-158)."""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(code, cwd):
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env, check=True, capture_output=True, timeout=120)
    return sorted(str(p.relative_to(cwd)) for p in Path(cwd).rglob("*"))


def test_importing_the_config_makes_no_folder(tmp_path):
    # BUG-284: `default_config = AppConfig()` ran at import and made data/output/{json,csv} and data/cache here.
    assert _run("import src.config; src.config.AppConfig()", tmp_path) == []


def test_a_read_only_browser_makes_no_folder(tmp_path):
    # BUG-158: a client session of the web app started here left data/cache and data/output/{csv,json}.
    db = tmp_path / "db" / "stock.db"
    code = ("from src.exporters.sqlite_store import SQLiteStore\n"
            f"SQLiteStore(db_path=r'{db}').export([])\n"
            "from fastapi.testclient import TestClient\n"
            "from src.webapp.app import create_app\n"
            f"c = TestClient(create_app(db_path=r'{db}'))\n"
            "assert c.get('/api/health').status_code == 200\n")
    created = _run(code, tmp_path)
    assert not [p for p in created if p.startswith("data")], created


def test_an_exporter_still_makes_its_own_folder(tmp_path):
    from src.exporters.json_exporter import JSONExporter
    JSONExporter(output_dir=tmp_path / "out" / "json")
    assert (tmp_path / "out" / "json").is_dir()
