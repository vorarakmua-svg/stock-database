"""A test-only writer of legacy Excel workbooks, BIFF8 records in an OLE2 compound file, to test the market data's own
reader (``src/market/xls.py``; docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md) on each record and
container feature it reads. Every workbook is built from the tests' own values: none of Shiller's data is here.

The records: ``record`` and the cell builders (``number``, ``rk``, ``mulrk``, ``labelsst``, ``label``, ``formula``
with ``cached_number`` or ``cached``, ``string_record``, ``boolerr``, ``blank``), the shared strings (``sst_head``,
``sst_string``: a test splits their bytes across the SST record and its CONTINUE records itself) and
``biff_stream``, the Workbook stream. The container: ``compound``, the streams under the root storage, the small ones
in the mini stream; ``pad_sectors`` free sectors first put the rest at high sector numbers, so the FAT outgrows the
header's 109 sectors and the DIFAT chain lists the others. ``workbook`` is the two together. ``repeated_fat`` is a
crafted file whose DIFAT lists one FAT sector over and over.
"""
import struct
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF
#: The FAT sectors the header lists itself; the DIFAT sectors list the others.
HEADER_FAT_SECTORS = 109
XF = 15  # every cell's format index: the reader ignores it

# ---- BIFF8 records ------------------------------------------------------------------------------------------------


def record(rtype: int, data: bytes = b"") -> bytes:
    return struct.pack("<HH", rtype, len(data)) + data


def bof(kind: int = 0x0010, version: int = 0x0600) -> bytes:
    """A BOF record: the workbook globals' (kind 0x0005), a worksheet's (0x0010) or an embedded chart's (0x0020)."""
    return record(0x0809, struct.pack("<HHHHII", version, kind, 0x0DBB, 0x07CC, 0, 0x0206))


EOF = record(0x000A)


def chars(text: str, high: bool) -> bytes:
    """*text*'s characters: UTF-16LE (*high*) or compressed, one byte each (Latin-1)."""
    return text.encode("utf-16-le" if high else "latin-1")


def xl_string(text: str, high: bool = False) -> bytes:
    """An XLUnicodeString (a LABEL's, a STRING's): its length in characters, the option byte, the characters."""
    return struct.pack("<HB", len(text), int(high)) + chars(text, high)


def number(row: int, col: int, value: float) -> bytes:
    return record(0x0203, struct.pack("<HHHd", row, col, XF, value))


def rk(row: int, col: int, raw: int) -> bytes:
    return record(0x027E, struct.pack("<HHHI", row, col, XF, raw))


def mulrk(row: int, first: int, raws: Sequence[int]) -> bytes:
    """A MULRK: the RK numbers of *row*'s columns from *first* on, then the last column's index."""
    cells = b"".join(struct.pack("<HI", XF, raw) for raw in raws)
    return record(0x00BD, struct.pack("<HH", row, first) + cells + struct.pack("<H", first + len(raws) - 1))


def labelsst(row: int, col: int, index: int) -> bytes:
    return record(0x00FD, struct.pack("<HHHI", row, col, XF, index))


def label(row: int, col: int, text: str, high: bool = False) -> bytes:
    return record(0x0204, struct.pack("<HHH", row, col, XF) + xl_string(text, high))


def blank(row: int, col: int) -> bytes:
    return record(0x0201, struct.pack("<HHH", row, col, XF))


#: A FORMULA's cached result that isn't a number: its type, in the first of its 8 bytes.
STRING_RESULT, BOOL_RESULT, ERROR_RESULT, EMPTY_RESULT = 0, 1, 2, 3


def cached_number(value: float) -> bytes:
    return struct.pack("<d", value)


def cached(kind: int, value: int = 0) -> bytes:
    """A cached result that isn't a number: its type, its value (a boolean's, an error's code) in the third byte,
    and 0xFFFF in the last two."""
    return bytes([kind, 0, value, 0, 0, 0, 0xFF, 0xFF])


def formula(row: int, col: int, result: bytes) -> bytes:
    """A FORMULA cell: its cached *result* (8 bytes), its options, and an empty expression."""
    return record(0x0006, struct.pack("<HHH", row, col, XF) + result + struct.pack("<HIH", 0, 0, 0))


def string_record(text: str, high: bool = False) -> bytes:
    """A STRING record: the string result of the FORMULA before it."""
    return record(0x0207, xl_string(text, high))


def boolerr(row: int, col: int, value: int, error: bool = False) -> bytes:
    return record(0x0205, struct.pack("<HHHBB", row, col, XF, value, int(error)))


def rk_int(n: int, div100: bool = False) -> int:
    """An RK holding the 30-bit signed integer *n* (divided by 100 when read, with *div100*)."""
    assert -(1 << 29) <= n < (1 << 29)
    return ((n << 2) & 0xFFFFFFFF) | 2 | int(div100)


def rk_float(value: float, div100: bool = False) -> int:
    """An RK holding *value*'s IEEE double, whose low 34 bits must be 0: the RK keeps the high 30."""
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    assert bits & ((1 << 34) - 1) == 0, f"{value} needs more than an RK's 30 bits"
    return (bits >> 32) | int(div100)


def sst_head(count: int) -> bytes:
    """The SST record's start: the strings' total and unique counts."""
    return struct.pack("<II", count, count)


def sst_string(text: str, high: bool = False, runs: int = 0, ext: bytes = b"") -> bytes:
    """A shared string whole (XLUnicodeRichExtendedString): its length, the option byte, the rich-text run count and
    the extended data's size where it has them, the characters, the runs (4 bytes each), the extended data."""
    flags = int(high) | (0x08 if runs else 0) | (0x04 if ext else 0)
    head = struct.pack("<HB", len(text), flags)
    if runs:
        head += struct.pack("<H", runs)
    if ext:
        head += struct.pack("<I", len(ext))
    return head + chars(text, high) + b"".join(struct.pack("<HH", i, i + 1) for i in range(runs)) + ext


def boundsheet(name: str, offset: int) -> bytes:
    """A BOUNDSHEET: the sheet's BOF offset in the stream, visible, a worksheet, its name (a ShortXLUnicodeString)."""
    high = any(ord(c) > 0xFF for c in name)
    return record(0x0085, struct.pack("<IBBBB", offset, 0, 0, len(name), int(high)) + chars(name, high))


def biff_stream(sheets: Sequence[Tuple[str, Sequence[bytes]]], sst: Sequence[bytes] = (),
                globals_extra: Sequence[bytes] = ()) -> bytes:
    """The Workbook stream: the globals (BOF, *globals_extra*, a BOUNDSHEET per sheet, the SST record holding the
    first item of *sst* and a CONTINUE record for each later one, EOF), then each sheet (BOF, its records, EOF).
    *sheets* are (name, records) pairs."""
    sst_records = b"".join(record(0x003C if i else 0x00FC, part) for i, part in enumerate(sst))
    bodies = [bof(0x0010) + b"".join(records) + EOF for _, records in sheets]
    head = bof(0x0005) + b"".join(globals_extra)
    offset = len(head) + sum(len(boundsheet(name, 0)) for name, _ in sheets) + len(sst_records) + len(EOF)
    offsets = []
    for body in bodies:
        offsets.append(offset)
        offset += len(body)
    sheet_records = b"".join(boundsheet(name, at) for (name, _), at in zip(sheets, offsets))
    return head + sheet_records + sst_records + EOF + b"".join(bodies)


# ---- the OLE2 compound file ---------------------------------------------------------------------------------------


def _ceil(n: int, size: int) -> int:
    return -(-n // size)


def _entry(name: str, kind: int, start: int, size: int, left: int = NOSTREAM, right: int = NOSTREAM,
           child: int = NOSTREAM) -> bytes:
    """A 128-byte directory entry."""
    raw = name.encode("utf-16-le") + b"\0\0" if name else b""
    return struct.pack("<64sHBBIII16sIQQIQ", raw, len(raw), kind, 1, left, right, child, b"\0" * 16, 0, 0, 0, start,
                       size)


def _tree(ids: List[int], links: Dict[int, Tuple[int, int]]) -> int:
    """Link *ids* (in name order) as a balanced binary tree, each node's (left, right) in *links*; its root."""
    if not ids:
        return NOSTREAM
    mid = len(ids) // 2
    links[ids[mid]] = (_tree(ids[:mid], links), _tree(ids[mid + 1:], links))
    return ids[mid]


def compound(streams: Sequence[Tuple[str, bytes]], sector_shift: int = 9, mini_shift: int = 6, cutoff: int = 4096,
             pad_sectors: int = 0, fat_patch: Optional[Mapping[int, int]] = None, size_garbage: int = 0) -> bytes:
    """An OLE2 compound file (version 3 with 512-byte sectors, 4 with 4096) holding *streams* (name, bytes) as the
    root storage's children, linked as a balanced tree in their order: a stream under *cutoff* bytes in the mini
    stream (mini sectors of 2**mini_shift bytes), the others in sectors of 2**sector_shift bytes. The streams of the
    FAT are laid out first, in their order, after *pad_sectors* free sectors; then the mini stream, the mini FAT,
    the directory, the FAT and the DIFAT sectors. *fat_patch* overrides FAT entries (sector -> next), to corrupt;
    *size_garbage* fills the high 32 bits of every entry's size (which version 3 leaves undefined)."""
    ss, ms = 1 << sector_shift, 1 << mini_shift
    fat: Dict[int, int] = {}
    content: Dict[int, bytes] = {}
    next_free = pad_sectors

    def allocate(data: bytes) -> int:
        nonlocal next_free
        if not data:
            return ENDOFCHAIN
        first, count = next_free, _ceil(len(data), ss)
        for i in range(count):
            content[first + i] = data[i * ss:(i + 1) * ss].ljust(ss, b"\0")
            fat[first + i] = first + i + 1 if i < count - 1 else ENDOFCHAIN
        next_free += count
        return first

    mini = bytearray()
    minifat: List[int] = []
    placed: List[Tuple[str, int, int]] = []
    for name, data in streams:
        if len(data) >= cutoff:
            placed.append((name, allocate(data), len(data)))
        elif not data:
            placed.append((name, ENDOFCHAIN, 0))
        else:
            first, count = len(mini) // ms, _ceil(len(data), ms)
            minifat += [first + i + 1 if i < count - 1 else ENDOFCHAIN for i in range(count)]
            mini += data.ljust(count * ms, b"\0")
            placed.append((name, first, len(data)))
    root_start = allocate(bytes(mini))
    per_sector = ss // 4
    minifat_sectors = _ceil(len(minifat), per_sector)
    minifat += [FREESECT] * (minifat_sectors * per_sector - len(minifat))
    minifat_start = allocate(struct.pack(f"<{len(minifat)}I", *minifat)) if minifat else ENDOFCHAIN

    links: Dict[int, Tuple[int, int]] = {}
    child = _tree(list(range(1, len(placed) + 1)), links)
    high = size_garbage << 32
    entries = [_entry("Root Entry", 5, root_start, high + len(mini), child=child)]
    for i, (name, start, size) in enumerate(placed, 1):
        left, right = links[i]
        entries.append(_entry(name, 2, start, high + size, left, right))
    per_dir_sector = ss // 128
    entries += [_entry("", 0, 0, 0)] * (_ceil(len(entries), per_dir_sector) * per_dir_sector - len(entries))
    dir_start = allocate(b"".join(entries))
    dir_sectors = len(entries) // per_dir_sector

    per_difat = per_sector - 1
    n_fat = n_difat = 0
    while True:  # the FAT covers every sector, its own and the DIFAT's too
        need_fat = _ceil(next_free + n_fat + n_difat, per_sector)
        need_difat = _ceil(max(0, need_fat - HEADER_FAT_SECTORS), per_difat)
        if (need_fat, need_difat) == (n_fat, n_difat):
            break
        n_fat, n_difat = need_fat, need_difat
    fat_sectors = list(range(next_free, next_free + n_fat))
    difat_sectors = list(range(next_free + n_fat, next_free + n_fat + n_difat))
    next_free += n_fat + n_difat
    for s in fat_sectors:
        fat[s] = FATSECT
    for s in difat_sectors:
        fat[s] = DIFSECT
    fat.update(fat_patch or {})
    table = [fat.get(i, FREESECT) for i in range(n_fat * per_sector)]
    for k, s in enumerate(fat_sectors):
        content[s] = struct.pack(f"<{per_sector}I", *table[k * per_sector:(k + 1) * per_sector])
    rest = fat_sectors[HEADER_FAT_SECTORS:]
    for k, s in enumerate(difat_sectors):
        listed = rest[k * per_difat:(k + 1) * per_difat]
        listed += [FREESECT] * (per_difat - len(listed))
        following = difat_sectors[k + 1] if k + 1 < len(difat_sectors) else ENDOFCHAIN
        content[s] = struct.pack(f"<{per_sector}I", *listed, following)

    in_header = fat_sectors[:HEADER_FAT_SECTORS]
    in_header += [FREESECT] * (HEADER_FAT_SECTORS - len(in_header))
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII", SIGNATURE, b"\0" * 16, 0x3E, 3 if ss == 512 else 4, 0xFFFE, sector_shift,
        mini_shift, b"\0" * 6, 0 if ss == 512 else dir_sectors, n_fat, dir_start, 0, cutoff, minifat_start,
        minifat_sectors, difat_sectors[0] if difat_sectors else ENDOFCHAIN, n_difat)
    header += struct.pack(f"<{HEADER_FAT_SECTORS}I", *in_header)
    body = bytearray(next_free * ss)
    for s, data in content.items():
        body[s * ss:(s + 1) * ss] = data
    return header.ljust(ss, b"\0") + bytes(body)


def repeated_fat(sector_shift: int = 12, difat_sectors: int = 2) -> bytes:
    """A crafted compound file (review 18's I-3) whose header and DIFAT list its one FAT sector over and over: the
    header counts 109 + *difat_sectors* x (a sector's entries - 1) FAT sectors, and every one listed is sector 0, in a
    file of *difat_sectors* + 2 sectors (the FAT, the DIFAT sectors, a directory holding the root entry alone). Read
    as listed, each listing adds a sector of FAT entries: with 4096-byte sectors and 2 DIFAT sectors, 2,155 x 1,024
    entries from a file of 20 KB."""
    ss = 1 << sector_shift
    per_sector = ss // 4
    n_fat = HEADER_FAT_SECTORS + difat_sectors * (per_sector - 1)
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII", SIGNATURE, b"\0" * 16, 0x3E, 3 if ss == 512 else 4, 0xFFFE, sector_shift, 6,
        b"\0" * 6, 1, n_fat, difat_sectors + 1, 0, 4096, ENDOFCHAIN, 0, 1, difat_sectors)
    header += struct.pack(f"<{HEADER_FAT_SECTORS}I", *([0] * HEADER_FAT_SECTORS))
    fat = [FATSECT] + [DIFSECT] * difat_sectors + [ENDOFCHAIN] + [FREESECT] * (per_sector - difat_sectors - 2)
    sectors = [struct.pack(f"<{per_sector}I", *fat)]
    for i in range(1, difat_sectors + 1):
        following = i + 1 if i < difat_sectors else ENDOFCHAIN
        sectors.append(struct.pack(f"<{per_sector}I", *([0] * (per_sector - 1) + [following])))
    sectors.append(_entry("Root Entry", 5, ENDOFCHAIN, 0).ljust(ss, b"\0"))
    return header.ljust(ss, b"\0") + b"".join(sectors)


#: The summary stream a workbook file carries beside its Workbook stream: 200 bytes, in the mini stream, before the
#: workbook's own mini sectors (so a small workbook never starts at the mini stream's first byte).
SUMMARY = ("\x05SummaryInformation", b"\xfe\xff" + b"S" * 198)


def workbook(sheets: Sequence[Tuple[str, Sequence[bytes]]], sst: Sequence[bytes] = (), pad_to: int = 0,
             **container: Any) -> bytes:
    """A compound file holding SUMMARY and "Workbook": ``biff_stream(sheets, sst)`` padded with zeros after its last
    EOF to *pad_to* bytes; *container* goes to ``compound``."""
    stream = biff_stream(sheets, sst)
    return compound([SUMMARY, ("Workbook", stream.ljust(pad_to, b"\0"))], **container)
