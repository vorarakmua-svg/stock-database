"""``read_only_uri`` (BUG-090) and the QA oracles' copies of it (BUG-089): every path form becomes a ``mode=ro`` URI
that names the same file, whatever characters it holds. The string tests open nothing; the opening ones stay in
tmp_path."""
import importlib.util
import sqlite3
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from src.exporters.sqlite_store import read_only_uri

ROOT = Path(__file__).resolve().parents[1]
ORACLES = ("history_vs_yahoo", "indicators_oracle", "market_oracle", "scores_oracle", "thirteenf_oracle",
           "valuation_oracle")

CASES = [
    # (path, the URI)
    (PurePosixPath("/srv/data/stock.db"), "file:/srv/data/stock.db?mode=ro"),
    (PurePosixPath("/srv/my data/stock.db"), "file:/srv/my data/stock.db?mode=ro"),
    (PurePosixPath("/srv/or#1/stock.db"), "file:/srv/or%231/stock.db?mode=ro"),
    (PurePosixPath("/srv/what?/stock.db"), "file:/srv/what%3f/stock.db?mode=ro"),
    (PurePosixPath("/srv/100%41/stock.db"), "file:/srv/100%2541/stock.db?mode=ro"),
    (PurePosixPath("/srv/caf\u00e9/\u6570\u636e.db"), "file:/srv/caf\u00e9/\u6570\u636e.db?mode=ro"),
    (PurePosixPath("//srv/data/stock.db"), "file:////srv/data/stock.db?mode=ro"),  # BUG-090: an empty authority
    (PurePosixPath("//srv/q?1/100%/p#1/stock.db"), "file:////srv/q%3f1/100%25/p%231/stock.db?mode=ro"),
    (PureWindowsPath("C:/dev/or#1/stock.db"), r"file:C:\dev\or%231\stock.db?mode=ro"),
    (PureWindowsPath("D:/my data/100%/stock.db"), r"file:D:\my data\100%25\stock.db?mode=ro"),
    (PureWindowsPath("C:/caf\u00e9/stock.db"), "file:C:\\caf\u00e9\\stock.db?mode=ro"),
    (PureWindowsPath("//srv/share/q?1/100%/proj#1/stock.db"),
     r"file:\\srv\share\q%3f1\100%25\proj%231\stock.db?mode=ro"),
    ("//srv/share/stock.db", "file:////srv/share/stock.db?mode=ro"),  # a string, as an oracle's --db arrives
    ("C:/dev/or#1/stock.db", "file:C:/dev/or%231/stock.db?mode=ro"),
    ("relative/or#1/stock.db", "file:relative/or%231/stock.db?mode=ro"),
]
IDS = [repr(str(path)) for path, _ in CASES]


def _load(name):
    spec = importlib.util.spec_from_file_location(f"qa_{name}", ROOT / "qa" / "oracle" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path, uri", CASES, ids=IDS)
def test_the_store_helper_escapes_and_names_an_empty_authority(path, uri):
    assert read_only_uri(path) == uri


@pytest.mark.parametrize("oracle", ORACLES)
@pytest.mark.parametrize("path, uri", CASES, ids=IDS)
def test_each_oracle_builds_the_same_uri(oracle, path, uri):
    assert _load(oracle).read_only_uri(str(path)) == uri


@pytest.mark.parametrize("oracle", ORACLES)
def test_no_oracle_opens_a_raw_database_uri_or_imports_src(oracle):
    text = (ROOT / "qa" / "oracle" / f"{oracle}.py").read_text(encoding="utf-8")
    assert 'connect(f"file:' not in text and "connect(read_only_uri(" in text
    assert "from src" not in text and "import src" not in text


@pytest.mark.parametrize("folder", ["or#1", "what?", "100%41", "my data", "caf\u00e9"])
def test_an_oracle_opens_a_database_under_any_folder_name(tmp_path, folder):
    db = tmp_path / folder / "stock.db"
    try:
        db.parent.mkdir()
    except OSError:
        pytest.skip("this file system refuses the name")
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE valuations (x)")
    conn.commit()
    conn.close()
    ro = sqlite3.connect(_load("valuation_oracle").read_only_uri(str(db)), uri=True)
    assert ro.execute("SELECT count(*) FROM valuations").fetchone() == (0,)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO valuations VALUES (1)")
    ro.close()
    assert [p.name for p in tmp_path.iterdir()] == [folder]
