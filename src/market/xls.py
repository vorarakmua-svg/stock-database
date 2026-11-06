"""A reader of legacy Excel workbooks (.xls: BIFF8 records in an OLE2 compound file), enough for Shiller's Data sheet
(docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md, "The market data"). No ``.xls`` reader is
installed and installing ``xlrd`` is the user's open question (Q14), so the milestone carries its own.

The container (MS-CFB): the 512-byte header (its signature; the sector and mini sector sizes from their shifts, 512 or
4096 bytes and 64; the first 109 FAT sectors, the DIFAT chain listing the others, at most the header's count of DIFAT
sectors; the directory's and the mini FAT's first sectors; the mini stream cutoff), the FAT, never more FAT sectors
than the file's sectors need nor one listed twice, and the directory, where the root storage's children are a tree of
left and right siblings.
The ``Workbook`` stream (or ``Book``) is read through its sector chain in the FAT, or, smaller than the cutoff,
through the mini FAT in the mini stream (the root entry's own chain).

The workbook (MS-XLS): records of a type (u16), a length (u16) and their data. The globals give the sheets (BOUNDSHEET:
each one's BOF offset in the stream and its name) and the shared strings (SST and its CONTINUE records: a string's
characters may break across a CONTINUE, whose data then starts with a fresh option byte, so compressed 8-bit and
UTF-16LE may switch; the rich-text runs and the extended data are skipped by their counts wherever they fall). A
sheet's cells: NUMBER, RK, MULRK, LABELSST, LABEL, FORMULA (its cached result: a number, or, with 0xFFFF in its last
two bytes, a string in the STRING record after it, a boolean, an error (no value) or an empty string) and BOOLERR (a
boolean; an error has no value). Every other record, and a chart embedded in the sheet, is skipped. A file that can't
be read raises XLSError: not a compound file or not BIFF8, a sector size MS-CFB doesn't have, a FAT counted beyond the
file's sectors or listing a sector twice, a sector chain that loops, leaves the file or ends short, a record that runs
past its stream. Every allocation is bounded by the file's size.
"""
import struct
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

#: A cell's value.
Cell = Union[float, str, bool]

#: An OLE2 compound file's first 8 bytes.
SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
#: The sector shifts MS-CFB has (512-byte sectors in version 3, 4096-byte ones in version 4) and its mini sector shift
#: (64 bytes). Any other is refused: a 64 KB sector would multiply what a crafted FAT allocates (review 18's I-3).
SECTOR_SHIFTS = (9, 12)
MINI_SECTOR_SHIFT = 6
#: The FAT's special entries (MS-CFB 2.1): regular sectors are numbered up to MAXREGSECT.
MAXREGSECT = 0xFFFFFFFA
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
#: A directory entry's sibling or child that isn't there.
NOSTREAM = 0xFFFFFFFF
#: The FAT sectors the header lists itself; the DIFAT sectors list the others.
HEADER_FAT_SECTORS = 109
#: The streams a workbook is in, by preference (names compare without case).
WORKBOOK_STREAMS = ("workbook", "book")
#: BIFF8's version in a BOF record.
BIFF8 = 0x0600

_BOF, _EOF, _BOUNDSHEET, _SST, _CONTINUE = 0x0809, 0x000A, 0x0085, 0x00FC, 0x003C
_NUMBER, _RK, _MULRK, _LABELSST, _LABEL = 0x0203, 0x027E, 0x00BD, 0x00FD, 0x0204
_FORMULA, _STRING, _BOOLERR = 0x0006, 0x0207, 0x0205
#: The cell records (BLANK and MULBLANK too): a formula's STRING record comes before the next of them.
_CELLS = frozenset({_NUMBER, _RK, _MULRK, _LABELSST, _LABEL, _FORMULA, _BOOLERR, 0x0201, 0x00BE})
#: A FORMULA's cached result types other than a number (2, an error, has no value).
_STRING_RESULT, _BOOL_RESULT, _EMPTY_RESULT = 0, 1, 3
_MALFORMED = (struct.error, IndexError)


class XLSError(ValueError):
    """A workbook that can't be read: not a compound file or not BIFF8, malformed or truncated."""


@dataclass
class _Entry:
    name: str
    kind: int  # 1 a storage, 2 a stream, 5 the root storage
    left: int
    right: int
    child: int
    start: int
    size: int


class _Compound:
    """An OLE2 compound file: its sectors, FAT, directory and streams."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != SIGNATURE:
            raise XLSError("not a legacy Excel workbook: no OLE2 compound file signature")
        self.data = data
        order, sector_shift, mini_shift = struct.unpack_from("<HHH", data, 0x1C)
        if order != 0xFFFE or sector_shift not in SECTOR_SHIFTS or mini_shift != MINI_SECTOR_SHIFT:
            raise XLSError(f"a malformed compound file header: byte order 0x{order:04X}, sector shift {sector_shift}, "
                           f"mini sector shift {mini_shift} (MS-CFB has 0xFFFE, 9 or 12, and 6)")
        self.sector_size, self.mini_size = 1 << sector_shift, 1 << mini_shift
        (_, n_fat, first_dir, _, self.cutoff, self.first_minifat, _, first_difat, n_difat) = struct.unpack_from(
            "<9I", data, 0x28)
        per_sector = self.sector_size // 4
        # The FAT needs an entry for each of the file's sectors and no more, so a count beyond that (a crafted DIFAT
        # listing one sector over and over) is refused before any FAT sector is read.
        n_sectors = -(-len(data) // self.sector_size) - 1
        most = -(-n_sectors // per_sector)
        if n_fat > most:
            raise XLSError(f"the header counts {n_fat} FAT sectors, more than the {most} that a file of {n_sectors} "
                           "sectors needs: it is truncated or damaged")
        listed = list(struct.unpack_from(f"<{HEADER_FAT_SECTORS}I", data, 0x4C))
        sector = first_difat
        seen: Set[int] = set()
        # At most the header's count of DIFAT sectors, each listing FAT sectors, then the next DIFAT sector.
        while sector not in (ENDOFCHAIN, FREESECT) and len(seen) < n_difat:
            if sector in seen:
                raise XLSError(f"the DIFAT chain loops at sector {sector}")
            seen.add(sector)
            entries = struct.unpack(f"<{per_sector}I", self._sector(sector))
            listed += entries[:-1]
            sector = entries[-1]
        if n_fat > len(listed):
            raise XLSError(f"the header counts {n_fat} FAT sectors and the DIFAT lists {len(listed)}")
        self.fat: List[int] = []
        fat_sectors: Set[int] = set()
        for sector in listed[:n_fat]:
            if sector in fat_sectors:
                raise XLSError(f"the DIFAT lists FAT sector {sector} twice: the file is damaged")
            fat_sectors.add(sector)
            self.fat += struct.unpack(f"<{per_sector}I", self._sector(sector))
        directory = self._join(self._chain(first_dir, self.fat, "the directory"))
        self.entries = []
        for at in range(0, len(directory) - 127, 128):
            raw, name_len, kind, _, left, right, child, _, _, _, _, start, size = struct.unpack_from(
                "<64sHBBIII16sIQQIQ", directory, at)
            name = raw[:max(0, min(name_len, 64) - 2)].decode("utf-16-le", "replace")
            if self.sector_size == 512:  # version 3: the size's high 32 bits may hold anything
                size &= 0xFFFFFFFF
            self.entries.append(_Entry(name, kind, left, right, child, start, size))
        if not self.entries or self.entries[0].kind != 5:
            raise XLSError("the compound file has no root storage")
        self._mini: Optional[Tuple[List[int], bytes]] = None

    def _sector(self, n: int) -> bytes:
        start = (n + 1) * self.sector_size
        if n > MAXREGSECT or start + self.sector_size > len(self.data):
            raise XLSError(f"sector {n} is outside the file ({len(self.data)} bytes): it is truncated or damaged")
        return self.data[start:start + self.sector_size]

    def _join(self, sectors: Sequence[int]) -> bytes:
        return b"".join(self._sector(s) for s in sectors)

    @staticmethod
    def _chain(start: int, table: Sequence[int], what: str) -> List[int]:
        """The sectors of the chain from *start* through *table* (the FAT, or the mini FAT)."""
        chain: List[int] = []
        seen = set()
        sector = start
        while sector != ENDOFCHAIN:
            if sector > MAXREGSECT or sector >= len(table):
                raise XLSError(f"{what}: its chain reaches sector {sector}, outside the file")
            if sector in seen:
                raise XLSError(f"{what}: its sector chain loops at sector {sector}")
            seen.add(sector)
            chain.append(sector)
            sector = table[sector]
        return chain

    def stream(self, entry: _Entry) -> bytes:
        """The stream of *entry*: from the mini stream when it is smaller than the cutoff."""
        if entry.size < self.cutoff:
            if self._mini is None:
                minifat = self._join(self._chain(self.first_minifat, self.fat, "the mini FAT"))
                root = self.entries[0]
                self._mini = (list(struct.unpack(f"<{len(minifat) // 4}I", minifat)),
                              self._join(self._chain(root.start, self.fat, "the mini stream"))[:root.size])
            table, mini = self._mini
            size = self.mini_size
            data = b"".join(mini[s * size:(s + 1) * size] for s in self._chain(entry.start, table, entry.name))
        else:
            data = self._join(self._chain(entry.start, self.fat, entry.name))
        if len(data) < entry.size:
            raise XLSError(f"the {entry.name} stream is short: its sectors hold {len(data)} of {entry.size} bytes")
        return data[:entry.size]

    def find(self, names: Sequence[str]) -> Optional[_Entry]:
        """The first of the streams *names* (without case) among the root storage's children."""
        found: Dict[str, _Entry] = {}
        pending, seen = [self.entries[0].child], set()
        while pending:
            i = pending.pop()
            if i == NOSTREAM:
                continue
            if i >= len(self.entries) or i in seen:
                raise XLSError("the compound file's directory tree is malformed")
            seen.add(i)
            entry = self.entries[i]
            if entry.kind == 2:
                found.setdefault(entry.name.lower(), entry)
            pending += [entry.left, entry.right]
        return next((found[name] for name in names if name in found), None)


def rk_number(raw: int) -> float:
    """An RK number: with bit 1, its high 30 bits are a signed integer, else a double's high 30 bits (the low 34 are
    0); with bit 0, it is divided by 100."""
    if raw & 2:
        value = float((raw - (1 << 32) if raw & 0x80000000 else raw) >> 2)
    else:
        value = struct.unpack("<d", struct.pack("<Q", (raw & 0xFFFFFFFC) << 32))[0]
    return value / 100 if raw & 1 else value


def _text(raw: bytes, high: int, count: int) -> str:
    width = 2 if high else 1
    if len(raw) < count * width:
        raise XLSError("a string runs past its record")
    return raw[:count * width].decode("utf-16-le" if high else "latin-1", "replace")


def _xl_string(data: bytes, at: int) -> str:
    """An XLUnicodeString at *at*: its length in characters (u16), its option byte, the characters."""
    count, flags = struct.unpack_from("<HB", data, at)
    return _text(data[at + 3:], flags & 1, count)


class _Pieces:
    """The SST record's data and its CONTINUE records' read as one sequence: a string's characters resume in the next
    record after a fresh option byte (the encoding may switch there); everything else runs on across the records."""

    def __init__(self, pieces: List[bytes]) -> None:
        self.pieces, self.index, self.pos = pieces, 0, 0

    def _piece(self) -> bytes:
        if self.index >= len(self.pieces):
            raise XLSError("the shared strings run past their records")
        return self.pieces[self.index]

    def take(self, n: int) -> bytes:
        out = []
        while n > 0:
            piece = self._piece()
            if self.pos >= len(piece):
                self.index, self.pos = self.index + 1, 0
                continue
            out.append(piece[self.pos:self.pos + n])
            self.pos += len(out[-1])
            n -= len(out[-1])
        return b"".join(out)

    def chars(self, count: int, high: bool) -> str:
        out = []
        while count > 0:
            piece = self._piece()
            if self.pos >= len(piece):  # the characters resume in the next record, after its option byte
                self.index += 1
                piece = self._piece()
                high, self.pos = bool(piece[0] & 1), 1
                continue
            width = 2 if high else 1
            n = min(count, (len(piece) - self.pos) // width)
            if n == 0:
                raise XLSError("a shared string's character is split across records")
            out.append(_text(piece[self.pos:self.pos + n * width], high, n))
            self.pos += n * width
            count -= n
        return "".join(out)


def _shared_strings(pieces: List[bytes]) -> List[str]:
    reader = _Pieces(pieces)
    _, unique = struct.unpack("<II", reader.take(8))
    strings = []
    for _ in range(unique):
        count, flags = struct.unpack("<HB", reader.take(3))
        runs = struct.unpack("<H", reader.take(2))[0] if flags & 0x08 else 0
        extended = struct.unpack("<I", reader.take(4))[0] if flags & 0x04 else 0
        strings.append(reader.chars(count, bool(flags & 0x01)))
        reader.take(4 * runs + extended)  # the rich-text runs (4 bytes each) and the extended data
    return strings


def _records(stream: bytes, offset: int, what: str) -> Iterator[Tuple[int, bytes]]:
    """The records of the substream whose BOF is at *offset*, its BOF first, up to its EOF; a substream nested in it
    (a chart embedded in a sheet) is skipped whole."""
    if stream[offset:offset + 2] != struct.pack("<H", _BOF):
        raise XLSError(f"{what}: no BOF record at offset {offset}")
    depth, pos = 0, offset
    while True:
        if pos + 4 > len(stream):
            raise XLSError(f"{what}: the stream ends before its EOF record")
        rtype, length = struct.unpack_from("<HH", stream, pos)
        if pos + 4 + length > len(stream):
            raise XLSError(f"{what}: record 0x{rtype:04X} at offset {pos} runs past the end of the stream")
        data, pos = stream[pos + 4:pos + 4 + length], pos + 4 + length
        if rtype == _BOF:
            depth += 1
        elif rtype == _EOF:
            depth -= 1
            if depth == 0:
                return
            continue
        if depth == 1:
            yield rtype, data


def _workbook(data: bytes) -> Tuple[bytes, List[Tuple[str, int]], List[str]]:
    """The Workbook stream, its sheets (name, BOF offset) in order, and its shared strings."""
    try:
        container = _Compound(data)
        entry = container.find(WORKBOOK_STREAMS)
        if entry is None:
            raise XLSError("the compound file has no Workbook stream")
        stream = container.stream(entry)
        sheets: List[Tuple[str, int]] = []
        pieces: List[bytes] = []
        in_sst = False
        for rtype, record in _records(stream, 0, "the workbook globals"):
            if rtype == _CONTINUE and in_sst:
                pieces.append(record)
                continue
            in_sst = rtype == _SST
            if rtype == _BOF:
                version = struct.unpack_from("<H", record)[0]
                if version != BIFF8:
                    raise XLSError(f"not a BIFF8 workbook: its BOF reads version 0x{version:04X}")
            elif rtype == _BOUNDSHEET:
                sheets.append((_text(record[8:], record[7] & 1, record[6]), struct.unpack_from("<I", record)[0]))
            elif rtype == _SST:
                pieces = [record]
        return stream, sheets, _shared_strings(pieces) if pieces else []
    except _MALFORMED as exc:
        raise XLSError(f"a malformed workbook: {exc}") from exc


def _cells(stream: bytes, offset: int, strings: List[str]) -> Dict[Tuple[int, int], Cell]:
    cells: Dict[Tuple[int, int], Cell] = {}
    pending: Optional[Tuple[int, int]] = None  # a formula's cell whose string is the next STRING record
    for rtype, data in _records(stream, offset, "the sheet"):
        if rtype == _STRING and pending is not None:
            cells[pending] = _xl_string(data, 0)
        if rtype in _CELLS or rtype == _STRING:
            pending = None
        if rtype == _NUMBER:
            row, col, _, value = struct.unpack_from("<HHHd", data)
            cells[(row, col)] = value
        elif rtype == _RK:
            row, col, _, raw = struct.unpack_from("<HHHI", data)
            cells[(row, col)] = rk_number(raw)
        elif rtype == _MULRK:
            row, first = struct.unpack_from("<HH", data)
            last = struct.unpack_from("<H", data, len(data) - 2)[0]
            if len(data) != 6 + 6 * (last - first + 1):
                raise XLSError(f"a MULRK of row {row} for columns {first} to {last} holds {len(data)} bytes")
            for i in range(last - first + 1):
                cells[(row, first + i)] = rk_number(struct.unpack_from("<I", data, 6 + 6 * i)[0])
        elif rtype == _LABELSST:
            row, col, _, index = struct.unpack_from("<HHHI", data)
            if index >= len(strings):
                raise XLSError(f"cell ({row}, {col}) names shared string {index} of {len(strings)}")
            cells[(row, col)] = strings[index]
        elif rtype == _LABEL:
            row, col = struct.unpack_from("<HH", data)
            cells[(row, col)] = _xl_string(data, 6)
        elif rtype == _FORMULA:
            row, col = struct.unpack_from("<HH", data)
            if data[12:14] != b"\xff\xff":
                cells[(row, col)] = struct.unpack_from("<d", data, 6)[0]
            elif data[6] == _STRING_RESULT:
                pending = (row, col)
            elif data[6] == _BOOL_RESULT:
                cells[(row, col)] = bool(data[8])
            elif data[6] == _EMPTY_RESULT:
                cells[(row, col)] = ""
        elif rtype == _BOOLERR:
            row, col, _, value, is_error = struct.unpack_from("<HHHBB", data)
            if not is_error:
                cells[(row, col)] = bool(value)
    return cells


def read_sheets(data: bytes) -> List[str]:
    """The workbook's sheet names, in order."""
    return [name for name, _ in _workbook(data)[1]]


def read_sheet(data: bytes, name: str) -> Dict[Tuple[int, int], Cell]:
    """The cells of the sheet *name*: (row, column), both from 0, to a number, a string or a boolean. Raises
    XLSError for a sheet the workbook doesn't have, or a workbook that can't be read."""
    stream, sheets, strings = _workbook(data)
    offset = next((at for sheet, at in sheets if sheet == name), None)
    if offset is None:
        raise XLSError(f"no sheet {name!r} in the workbook (its sheets: {', '.join(repr(s) for s, _ in sheets)})")
    try:
        return _cells(stream, offset, strings)
    except _MALFORMED as exc:
        raise XLSError(f"the sheet {name!r} is malformed: {exc}") from exc
