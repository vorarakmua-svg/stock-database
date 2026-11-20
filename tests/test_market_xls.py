"""The market data's own reader of legacy Excel workbooks (``src/market/xls.py``; docs/superpowers/specs/
2026-10-24-valuation-tools-market-design.md, "The market data"): the OLE2 compound file's header, FAT and DIFAT chain,
directory and mini stream, with their bounds (MS-CFB's sector sizes only, the DIFAT walk at the header's count, a FAT
no larger than the file's sectors need and no FAT sector twice), then the BIFF8 records: the sheets (BOUNDSHEET), the
shared strings (SST and its CONTINUE records) and the cells (NUMBER, RK, MULRK, LABELSST, LABEL, FORMULA with
STRING, BOOLERR). Every workbook is written by the tests' own writer (``tests/market_xls_writer.py``); the real
Shiller workbook is read in the real-data check, never committed.
"""
import struct
import time
import tracemalloc

import pytest

from src.market.xls import XLSError, read_sheet, read_sheets
from tests import market_xls_writer as w


def _sheet(records, sst=(), **container):
    """The one sheet "Sheet1" of a workbook holding *records*, read back."""
    return read_sheet(w.workbook([("Sheet1", records)], sst=sst, **container), "Sheet1")


def _column(rows):
    """A sheet's records numbering *rows* rows down column 0 (18 bytes each): 0.0, 1.0, ..."""
    return [w.number(r, 0, float(r)) for r in range(rows)]


# ---- cells --------------------------------------------------------------------------------------------------------


def test_a_number_is_read_at_its_row_and_column():
    assert _sheet([w.number(0, 0, 1871.01), w.number(3, 7, -2.5)]) == {(0, 0): 1871.01, (3, 7): -2.5}


def test_each_kind_of_rk_number():
    # Bit 0 divides by 100; bit 1 makes the high 30 bits a signed integer, else they are a double's high 30 bits.
    cells = _sheet([w.rk(0, 0, w.rk_float(1.5)), w.rk(0, 1, w.rk_float(12.5, div100=True)),
                    w.rk(0, 2, w.rk_int(-7)), w.rk(0, 3, w.rk_int(187101, div100=True)),
                    w.rk(0, 4, w.rk_int(536870911)), w.rk(0, 5, w.rk_int(-536870912, div100=True))])
    assert cells == {(0, 0): 1.5, (0, 1): 0.125, (0, 2): -7.0, (0, 3): 1871.01, (0, 4): 536870911.0,
                     (0, 5): -5368709.12}
    assert all(type(value) is float for value in cells.values())


def test_a_mulrk_gives_each_column_from_its_first_to_its_last():
    raws = [w.rk_int(1), w.rk_float(2.5), w.rk_int(300, div100=True)]
    assert _sheet([w.mulrk(4, 2, raws), w.number(4, 5, 9.0)]) == {(4, 2): 1.0, (4, 3): 2.5, (4, 4): 3.0,
                                                                   (4, 5): 9.0}


def test_labels_from_the_shared_strings_and_inline():
    sst = [w.sst_head(2) + w.sst_string("Date") + w.sst_string("Price \u20ac", high=True)]
    records = [w.labelsst(0, 0, 0), w.labelsst(0, 1, 1), w.labelsst(2, 0, 0), w.label(1, 0, "CPI"),
               w.label(1, 1, "\u0152uvre", high=True)]
    assert _sheet(records, sst) == {(0, 0): "Date", (0, 1): "Price \u20ac", (2, 0): "Date", (1, 0): "CPI",
                                    (1, 1): "\u0152uvre"}


def test_a_formulas_cached_result():
    # A number; a string, in the STRING record after the FORMULA (a shared formula's record may come between); a
    # boolean; an error (no value); an empty string.
    records = [w.formula(0, 0, w.cached_number(4.25)),
               w.formula(0, 1, w.cached(w.STRING_RESULT)), w.string_record("NA"),
               w.formula(0, 2, w.cached(w.BOOL_RESULT, 1)),
               w.formula(0, 3, w.cached(w.ERROR_RESULT, 0x07)),
               w.formula(0, 4, w.cached(w.EMPTY_RESULT)),
               w.formula(0, 5, w.cached(w.STRING_RESULT)), w.record(0x04BC, bytes(8)),
               w.string_record("\u20ac 2", high=True),
               w.formula(0, 6, w.cached_number(-0.5)), w.string_record("not a result"),
               w.formula(0, 7, w.cached(w.BOOL_RESULT, 0))]
    assert _sheet(records) == {(0, 0): 4.25, (0, 1): "NA", (0, 2): True, (0, 4): "", (0, 5): "\u20ac 2",
                               (0, 6): -0.5, (0, 7): False}


def test_a_boolean_is_read_and_an_error_skipped():
    assert _sheet([w.boolerr(0, 0, 1), w.boolerr(0, 1, 0), w.boolerr(0, 2, 0x2A, error=True)]) == {
        (0, 0): True, (0, 1): False}


def test_other_records_are_skipped():
    records = [w.record(0x0200, bytes(14)), w.record(0x0208, bytes(16)), w.blank(0, 0), w.number(0, 1, 3.0),
               w.record(0x00BE, struct.pack("<HHHHH", 0, 2, 15, 15, 3))]
    assert _sheet(records) == {(0, 1): 3.0}


def test_a_chart_embedded_in_a_sheet_is_skipped():
    records = [w.number(0, 0, 1.0), w.bof(0x0020), w.number(5, 5, 99.0), w.EOF, w.number(1, 0, 2.0)]
    assert _sheet(records) == {(0, 0): 1.0, (1, 0): 2.0}


# ---- the shared strings -------------------------------------------------------------------------------------------


def _labels(n):
    return [w.labelsst(0, i, i) for i in range(n)]


def test_a_string_split_across_continue_records_resumes_after_a_fresh_option_byte():
    # "Earnings" starts compressed in the SST record and ends in UTF-16 in the first CONTINUE record, which starts
    # with the option byte 0x01; "Euro \u20ac index" starts in UTF-16 there and ends compressed in the second, after
    # the option byte 0x00; "CAPE" follows it whole.
    sst = [w.sst_head(3) + struct.pack("<HB", 8, 0) + b"Earn",
           b"\x01" + "ings".encode("utf-16-le") + struct.pack("<HB", 12, 1) + "Euro \u20ac".encode("utf-16-le"),
           b"\x00" + b" index" + w.sst_string("CAPE")]
    assert _sheet(_labels(3), sst) == {(0, 0): "Earnings", (0, 1): "Euro \u20ac index", (0, 2): "CAPE"}


def test_a_string_ending_with_its_record_leaves_the_next_one_whole_in_the_continue():
    sst = [w.sst_head(2) + w.sst_string("Date"), w.sst_string("Price")]
    assert _sheet(_labels(2), sst) == {(0, 0): "Date", (0, 1): "Price"}


def test_characters_starting_a_continue_record_come_after_its_option_byte():
    sst = [w.sst_head(1) + struct.pack("<HB", 3, 0), b"\x01" + "CPI".encode("utf-16-le")]
    assert _sheet(_labels(1), sst) == {(0, 0): "CPI"}


def test_rich_text_runs_and_extended_data_are_skipped_by_their_counts_wherever_they_fall():
    rich = w.sst_string("Rich", runs=2)
    phonetic = w.sst_string("Phonetic", ext=b"\x01" * 13)
    both = w.sst_string("Both \u20ac", high=True, runs=3, ext=b"\x02" * 5)
    payload = w.sst_head(4) + rich + phonetic + both + w.sst_string("After")
    # Split inside the first string's runs, the second's extended data and the third's runs: no option byte there.
    inside_runs = 8 + 3 + 2 + 4 + 3
    inside_ext = 8 + len(rich) + 3 + 4 + 8 + 6
    inside_both = 8 + len(rich) + len(phonetic) + 3 + 2 + 4 + 12 + 5
    cuts = [0, inside_runs, inside_ext, inside_both, len(payload)]
    sst = [payload[a:b] for a, b in zip(cuts, cuts[1:])]
    assert _sheet(_labels(4), sst) == {(0, 0): "Rich", (0, 1): "Phonetic", (0, 2): "Both \u20ac", (0, 3): "After"}
    assert _sheet(_labels(4), [payload]) == {(0, 0): "Rich", (0, 1): "Phonetic", (0, 2): "Both \u20ac",
                                             (0, 3): "After"}


def test_a_shared_string_index_out_of_range_raises():
    with pytest.raises(XLSError):
        _sheet([w.labelsst(0, 0, 5)], [w.sst_head(1) + w.sst_string("x")])


# ---- sheets -------------------------------------------------------------------------------------------------------


def test_each_sheet_is_found_by_its_name():
    data = w.workbook([("Notes", [w.label(0, 0, "about")]), ("Data", [w.number(0, 0, 1.0)]),
                       ("Donn\xe9es \u20ac", [w.number(1, 1, 2.0)])])
    assert read_sheets(data) == ["Notes", "Data", "Donn\xe9es \u20ac"]
    assert read_sheet(data, "Data") == {(0, 0): 1.0}
    assert read_sheet(data, "Notes") == {(0, 0): "about"}
    assert read_sheet(data, "Donn\xe9es \u20ac") == {(1, 1): 2.0}


def test_an_unknown_sheet_raises_naming_the_workbooks_sheets():
    data = w.workbook([("Notes", []), ("Data", [])])
    with pytest.raises(XLSError, match="Prices.*Notes.*Data"):
        read_sheet(data, "Prices")


def test_a_biff5_workbook_raises():
    data = w.compound([("Book", w.bof(0x0005, version=0x0500) + w.EOF)])
    with pytest.raises(XLSError, match="BIFF8"):
        read_sheets(data)


# ---- the compound file --------------------------------------------------------------------------------------------


def test_a_workbook_stream_above_the_mini_stream_cutoff_is_read_through_the_fat():
    records = _column(600)
    assert len(w.biff_stream([("Sheet1", records)])) > 8 * 512  # several sectors
    cells = _sheet(records)
    assert len(cells) == 600 and cells[(599, 0)] == 599.0


def test_a_workbook_stream_of_exactly_the_cutoff_is_in_the_fat_and_one_byte_less_in_the_mini_stream():
    for size in (4096, 4095):
        assert _sheet(_column(3), pad_to=size) == {(0, 0): 0.0, (1, 0): 1.0, (2, 0): 2.0}


def test_the_mini_stream_cutoff_is_the_headers():
    # 5.4 KB under a cutoff of 8 KB: in the mini stream, after the summary stream's mini sectors.
    data = w.workbook([("Sheet1", _column(300))], cutoff=8192)
    assert struct.unpack_from("<I", data, 0x38)[0] == 8192
    assert read_sheet(data, "Sheet1")[(299, 0)] == 299.0


def test_a_small_workbook_stream_is_read_from_the_mini_stream_after_another_stream():
    stream = w.biff_stream([("Sheet1", _column(20))])
    assert len(stream) < 4096
    data = w.compound([("\x05SummaryInformation", b"S" * 300), ("Workbook", stream)])
    cells = read_sheet(data, "Sheet1")
    assert len(cells) == 20 and cells[(19, 0)] == 19.0


def test_a_sector_size_ms_cfb_doesnt_have_raises():
    # Review 18's I-3: MS-CFB has 512- and 4096-byte sectors (shifts 9 and 12) and 64-byte mini sectors (shift 6). A
    # 64 KB sector would multiply what a crafted FAT allocates, so the files below, well formed otherwise, are refused.
    for container in ({"sector_shift": 10}, {"sector_shift": 11}, {"sector_shift": 16}, {"mini_shift": 5},
                      {"mini_shift": 7}):
        with pytest.raises(XLSError, match="sector shift"):
            read_sheets(w.workbook([("Sheet1", _column(20))], **container))
    for shift in (9, 12):
        assert read_sheet(w.workbook([("Sheet1", _column(20))], sector_shift=shift), "Sheet1")[(19, 0)] == 19.0


def test_the_workbook_is_found_among_the_root_storages_children_on_either_side():
    stream = w.biff_stream([("Sheet1", _column(300))])
    for streams in ([("Workbook", stream), ("\x05SummaryInformation", b"S" * 5000), ("\x05Doc", b"D" * 200)],
                    [("\x05Doc", b"D" * 200), ("\x05SummaryInformation", b"S" * 5000), ("Workbook", stream)]):
        assert read_sheet(w.compound(streams), "Sheet1")[(299, 0)] == 299.0


def test_a_book_stream_is_read_too():
    data = w.compound([("Book", w.biff_stream([("Sheet1", _column(2))]))])
    assert read_sheet(data, "Sheet1") == {(0, 0): 0.0, (1, 0): 1.0}


def test_a_version_4_file_with_4096_byte_sectors():
    data = w.workbook([("Sheet1", _column(1000))], sector_shift=12)
    assert struct.unpack_from("<HH", data, 0x1A) == (4, 0xFFFE) and struct.unpack_from("<H", data, 0x1E)[0] == 12
    cells = read_sheet(data, "Sheet1")
    assert len(cells) == 1000 and cells[(999, 0)] == 999.0


def test_a_version_3_streams_size_is_its_low_32_bits():
    # Version 3 leaves a stream size's high 32 bits undefined, and some writers fill them.
    stream = w.biff_stream([("Sheet1", _column(300))])
    data = w.compound([w.SUMMARY, ("Workbook", stream)], size_garbage=0xDEADBEEF)
    directory = 512 * (1 + struct.unpack_from("<I", data, 0x30)[0])
    assert struct.unpack_from("<Q", data, directory + 2 * 128 + 0x78)[0] == (0xDEADBEEF << 32) + len(stream)
    assert read_sheet(data, "Sheet1")[(299, 0)] == 299.0


def test_the_fat_past_the_headers_109_sectors_is_found_through_the_difat_chain():
    # 30,400 free sectors first put the workbook's sectors past 236 x 128: their FAT entries sit in FAT sectors that
    # only the second DIFAT sector lists.
    data = w.workbook([("Sheet1", _column(300))], pad_sectors=30400)
    n_fat, = struct.unpack_from("<I", data, 0x2C)
    n_difat, = struct.unpack_from("<I", data, 0x48)
    assert n_fat > w.HEADER_FAT_SECTORS + 127 and n_difat == 2
    assert read_sheet(data, "Sheet1")[(299, 0)] == 299.0


def test_the_difat_walk_stops_after_the_headers_count_of_difat_sectors():
    # The second and last DIFAT sector's next pointer points back at the first instead of ending the chain: the
    # header counts two DIFAT sectors, so the walk stops there and never follows it.
    data = bytearray(w.workbook([("Sheet1", _column(300))], pad_sectors=30400))
    first, n_difat = struct.unpack_from("<II", data, 0x44)
    second = struct.unpack_from("<I", data, (first + 1) * 512 + 508)[0]
    assert n_difat == 2 and struct.unpack_from("<I", data, (second + 1) * 512 + 508)[0] == w.ENDOFCHAIN
    struct.pack_into("<I", data, (second + 1) * 512 + 508, first)
    assert read_sheet(bytes(data), "Sheet1")[(299, 0)] == 299.0


def _file_sectors(data, size=512):
    """The sectors a compound file holds after its header, and the FAT sectors that many need."""
    sectors = -(-len(data) // size) - 1
    return sectors, -(-sectors // (size // 4))


def test_a_fat_counted_beyond_what_the_files_sectors_need_raises():
    # The header counts one FAT sector more than the file's sectors need, the extra one listed as a data sector.
    data = bytearray(w.workbook([("Sheet1", _column(600))]))
    sectors, needed = _file_sectors(data)
    assert struct.unpack_from("<I", data, 0x2C)[0] == needed == 1
    assert read_sheet(bytes(data), "Sheet1")[(599, 0)] == 599.0
    struct.pack_into("<I", data, 0x2C, needed + 1)
    struct.pack_into("<I", data, 0x4C + 4 * needed, 1)
    with pytest.raises(XLSError, match=f"counts 2 FAT sectors, more than the 1 that a file of {sectors} sectors"):
        read_sheet(bytes(data), "Sheet1")


def test_a_fat_sector_listed_twice_raises():
    # 200 free sectors first: the file needs two FAT sectors, and the header lists the first one twice.
    data = bytearray(w.workbook([("Sheet1", _column(300))], pad_sectors=200))
    assert struct.unpack_from("<I", data, 0x2C)[0] == _file_sectors(data)[1] == 2
    first = struct.unpack_from("<I", data, 0x4C)[0]
    struct.pack_into("<I", data, 0x50, first)
    with pytest.raises(XLSError, match=f"lists FAT sector {first} twice"):
        read_sheet(bytes(data), "Sheet1")


def test_a_fat_listed_over_and_over_raises_at_once_without_a_large_allocation():
    # Review 18's I-3: 20 KB whose header counts 2,155 FAT sectors, every one of them sector 0. Read as listed, that
    # is 2.2 million FAT entries (about 90 MB); it must be refused before any of them is read.
    data = w.repeated_fat(sector_shift=12, difat_sectors=2)
    assert len(data) == 20480 and struct.unpack_from("<I", data, 0x2C)[0] == 109 + 2 * 1023
    tracemalloc.start()
    try:
        started = time.perf_counter()
        with pytest.raises(XLSError, match="FAT sector"):
            read_sheets(data)
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000 and elapsed < 1.0, (peak, elapsed)


def test_no_workbook_stream_raises():
    with pytest.raises(XLSError, match="Workbook"):
        read_sheets(w.compound([("Other", b"x" * 100)]))


def test_a_file_that_isnt_a_compound_file_raises():
    for data in (b"", b"PK\x03\x04" + bytes(1000), bytes(512)):
        with pytest.raises(XLSError):
            read_sheets(data)


def test_a_truncated_file_raises():
    data = w.workbook([("Sheet1", _column(600))])
    for size in (100, 511, 512, 512 + 600, len(data) // 2, len(data) - 512, len(data) - 1):
        with pytest.raises(XLSError):
            read_sheet(data[:size], "Sheet1")


def test_a_truncated_workbook_stream_raises():
    stream = w.biff_stream([("Sheet1", _column(600))])
    for size in (10, 5000, len(stream) - 10):
        with pytest.raises(XLSError):
            read_sheet(w.compound([("Workbook", stream[:size])]), "Sheet1")


def test_a_sector_chain_that_loops_ends_short_or_leaves_the_file_raises():
    stream = w.biff_stream([("Sheet1", _column(600))])
    last = -(-len(stream) // 512) - 1  # the workbook's sectors are the file's first: 0 to last
    for patch, match in (({last: 0}, "loop"), ({5: w.ENDOFCHAIN}, "short"), ({0: 0x00100000}, "outside"),
                         ({3: w.FREESECT}, "outside")):
        with pytest.raises(XLSError, match=match):
            read_sheet(w.compound([("Workbook", stream)], fat_patch=patch), "Sheet1")
