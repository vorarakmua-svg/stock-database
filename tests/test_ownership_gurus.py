"""The curated guru list (Milestone 3): each guru's 13F filers, checked against their SEC submissions feeds
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md; the check is qa/evidence/ownership-m3-4-gurus-check.txt).
"""
from src.ownership.gurus import GURUS, guru_by_cik


def test_every_filer_cik_belongs_to_one_guru():
    ciks = [cik for g in GURUS for cik in g.ciks]
    assert len(ciks) == len(set(ciks))
    assert all(cik.isdigit() and not cik.startswith("0") for cik in ciks)


def test_each_guru_has_a_name_a_manager_and_its_current_filer_first():
    assert len(GURUS) >= 30
    for g in GURUS:
        assert g.name and g.manager and g.ciks[0] == g.cik


def test_a_guru_reads_its_earlier_filers_too():
    # Pershing Square Inc. reports Bill Ackman's portfolio from Q2 2026; Pershing Square Capital Management, L.P.
    # did before, and its Q2 2026 filing is a notice pointing to the new filer (13F-NT).
    ackman = guru_by_cik("2026053")
    assert ackman is not None and ackman.manager == "Bill Ackman" and ackman.ciks == ("2026053", "1336528")
    assert ackman.filers == (("2026053", "PERSHING SQUARE INC."),
                             ("1336528", "Pershing Square Capital Management, L.P."))
    assert guru_by_cik("1336528") is ackman  # an earlier filer finds its guru
    assert guru_by_cik("123") is None
