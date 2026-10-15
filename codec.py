"""Encode an ATC database image into an ACARS VHF injector script, and decode it back.

ACARS VHF text is at most 220 ISO-5 characters per air block, and the text
field cannot carry raw binary. This codec hex-encodes the file, slices it so
every text field is <= 220 characters, and groups the slices into ARINC 618
multiblock messages of at most 16 blocks.

The .vhf file uses the same injector tags as VAQ_Test_999Chars.vhf
(<SOH>, <STX>, <ETX>, DELAY=) and adds <ETB> for every block that is not the
last block of a message.
"""

from __future__ import annotations

import argparse
import re
import sys
import zlib
from dataclasses import dataclass, field

TEXT_MAX = 220
HEX_BUDGET = 188  # 220 - len("ATDB/U/SSSS/BBB/TTT/F/OOOO/") - len("/CCCC")
BYTES_PER_SLICE = HEX_BUDGET // 2  # 94
DEFAULT_BLOCKS_PER_MESSAGE = 16
BLOCK_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
SAFE_TEXT = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/")
ATS_LABELS = {f"A{d}" for d in "0123456789"} | {f"B{d}" for d in "0123456789"}
HEADER_LEN = 0x28
RECORD_LEN = 55
TRAILER_MOD = 24


class CodecError(ValueError):
    """The bin or the .vhf file cannot be converted."""


@dataclass
class Centre:
    index: int
    gfd: str
    name: str
    country: str
    tsel: str
    tsel_hex: str


@dataclass
class BinInfo:
    size: int
    file_crc32: str
    recognized: bool
    db_id: str = ""
    part: str = ""
    slots: int = 0
    centres: list[Centre] = field(default_factory=list)
    empty_slots: int = 0
    trailer_hex: str = ""


@dataclass
class EncodeResult:
    text: str
    session: str
    size: int
    file_crc32: str
    data_slices: int
    air_blocks: int
    messages: int
    max_text: int
    label: str
    warnings: list[str] = field(default_factory=list)


def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def file_crc32(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace")


def inspect_bin(data: bytes) -> BinInfo:
    info = BinInfo(size=len(data), file_crc32=file_crc32(data), recognized=False)
    if len(data) < HEADER_LEN + RECORD_LEN + TRAILER_MOD:
        return info
    body_len = len(data) - HEADER_LEN
    if body_len % RECORD_LEN != TRAILER_MOD:
        return info
    part = _cstr(data[0x14:0x20])
    db_id = _cstr(data[4:10])
    if not re.fullmatch(r"[0-9A-Z-]{6,16}", part):
        return info
    if not re.fullmatch(r"[A-Z0-9]{4,8}", db_id):
        return info
    slots = body_len // RECORD_LEN
    centres: list[Centre] = []
    for index in range(slots):
        rec = data[HEADER_LEN + index * RECORD_LEN : HEADER_LEN + (index + 1) * RECORD_LEN]
        gfd = _cstr(rec[0:8])
        if not re.fullmatch(r"[A-Z]{4}", gfd):
            continue
        name = _cstr(rec[9:20])
        country = rec[26:28].decode("ascii", "replace")
        tsel = rec[53:55]
        tsel_txt = tsel.decode("ascii", "replace") if all(32 <= b < 127 for b in tsel) else tsel.hex().upper()
        centres.append(
            Centre(
                index=index,
                gfd=gfd,
                name=name,
                country=country,
                tsel=tsel_txt,
                tsel_hex=tsel.hex().upper(),
            )
        )
    info.recognized = True
    info.db_id = db_id
    info.part = part
    info.slots = slots
    info.centres = centres
    info.empty_slots = slots - len(centres)
    info.trailer_hex = data[-TRAILER_MOD:].hex().upper()
    return info


def _wrap(session: str, blk: int, tot: int, body: str) -> str:
    head = f"ATDB/U/{session}/{blk:03d}/{tot:03d}/{body}"
    text = f"{head}/{crc16_ccitt(head.encode('ascii')):04X}"
    if len(text) > TEXT_MAX:
        raise CodecError(f"text field is {len(text)} characters; VHF limit is {TEXT_MAX}")
    if any(ch not in SAFE_TEXT for ch in text):
        raise CodecError("uplink text left the ACARS-safe alphabet")
    return text


def _warnings(label: str, delay_ms: int, air_blocks: int) -> list[str]:
    notes: list[str] = []
    if label in ATS_LABELS or label == "H1":
        notes.append(f"Label {label} is an ATS label. This uplink uses C1.")
    if label == "RA":
        notes.append("Label RA is a command uplink many CMUs display. Do not use it for a database load.")
    if delay_ms >= 5000:
        notes.append(
            f"DELAY={delay_ms} is a long pause after every block. "
            "A database load should wait about 1000 ms, not 30000."
        )
    if air_blocks > DEFAULT_BLOCKS_PER_MESSAGE:
        seconds = air_blocks * max(delay_ms, 800) / 1000
        notes.append(
            f"This is a full-image load: {air_blocks} VHF blocks, about {seconds:.0f} s of injector time. "
            "A later row-patch of one 55-byte centre is a single message."
        )
    return notes


def encode_vhf(
    data: bytes,
    *,
    label: str = "C1",
    address: str = "#ID#",
    ack: str = "ACK",
    delay_ms: int = 1000,
    mode: str = "?",
    blocks_per_message: int = DEFAULT_BLOCKS_PER_MESSAGE,
) -> EncodeResult:
    if not data:
        raise CodecError("ATC database file is empty")
    if len(data) > 0xFFFF:
        raise CodecError("file is larger than 65535 bytes; the 4-digit offset field cannot address it")
    label = label.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2}", label):
        raise CodecError("label must be exactly 2 letters or digits")
    if address != "#ID#" and (not 1 <= len(address) <= 7 or any(c in address for c in "<>")):
        raise CodecError("address must be #ID# or 1 to 7 characters")
    ack = ack.strip().upper()
    if ack not in {"ACK", "NAK"}:
        raise CodecError("ack must be ACK or NAK")
    if not isinstance(delay_ms, int) or delay_ms < 0 or delay_ms > 120000:
        raise CodecError("delay must be 0 to 120000 milliseconds")
    if len(mode) != 1 or mode in "<>":
        raise CodecError("mode must be one character")
    if not 1 <= blocks_per_message <= 16:
        raise CodecError("blocks per message must be 1 to 16")

    slices = [data[i : i + BYTES_PER_SLICE] for i in range(0, len(data), BYTES_PER_SLICE)]
    if len(slices) > 999:
        raise CodecError("file needs more than 999 slices")
    session = file_crc32(data)[:4]
    crc = file_crc32(data)
    texts = [_wrap(session, 0, len(slices), f"H/{len(data):08X}/{crc}")]
    for index, chunk in enumerate(slices, start=1):
        texts.append(_wrap(session, index, len(slices), f"F/{(index - 1) * BYTES_PER_SLICE:04X}/{chunk.hex().upper()}"))

    lines: list[str] = []
    for start in range(0, len(texts), blocks_per_message):
        group = texts[start : start + blocks_per_message]
        for pos, text in enumerate(group):
            last = pos == len(group) - 1
            suffix = "ETX" if last else "ETB"
            block_id = BLOCK_IDS[pos]
            lines.append(
                f"<SOH>{mode}{address}<{ack}>{label}{block_id}<STX>{text}<{suffix}>\r\n"
                f"DELAY={delay_ms}\r\n"
            )
    rendered = "".join(lines)
    return EncodeResult(
        text=rendered,
        session=session,
        size=len(data),
        file_crc32=crc,
        data_slices=len(slices),
        air_blocks=len(texts),
        messages=(len(texts) + blocks_per_message - 1) // blocks_per_message,
        max_text=max(len(t) for t in texts),
        label=label,
        warnings=_warnings(label, delay_ms, len(texts)),
    )


def _iter_texts(vhf: str) -> list[str]:
    texts: list[str] = []
    cursor = 0
    while True:
        start = vhf.find("<STX>", cursor)
        if start < 0:
            break
        etx = vhf.find("<ETX>", start)
        etb = vhf.find("<ETB>", start)
        ends = [pos for pos in (etx, etb) if pos >= 0]
        if not ends:
            raise CodecError("uplink has <STX> without <ETX> or <ETB>")
        end = min(ends)
        texts.append(vhf[start + 5 : end])
        cursor = end + 5
    if not texts:
        raise CodecError("no ACARS text fields found")
    return texts


def decode_vhf(vhf: str) -> bytes:
    size: int | None = None
    expect_crc = ""
    expect_slices: int | None = None
    session = ""
    chunks: dict[int, bytes] = {}
    for text in _iter_texts(vhf):
        if "/" not in text:
            raise CodecError("uplink text has no CRC")
        head, crc_txt = text.rsplit("/", 1)
        if len(crc_txt) != 4 or any(c not in "0123456789ABCDEF" for c in crc_txt):
            raise CodecError("uplink CRC is not 4 hex characters")
        actual = f"{crc16_ccitt(head.encode('ascii')):04X}"
        if actual != crc_txt:
            raise CodecError(f"block CRC mismatch: file has {crc_txt}, computed {actual}")
        parts = head.split("/", 5)
        if len(parts) != 6 or parts[0] != "ATDB" or parts[1] != "U":
            raise CodecError("uplink text is not an ATDB block")
        blk_s, tot_s, body = parts[3], parts[4], parts[5]
        if session and parts[2] != session:
            raise CodecError("uplink file mixes sessions")
        session = parts[2]
        try:
            blk = int(blk_s, 10)
            tot = int(tot_s, 10)
        except ValueError as exc:
            raise CodecError("block numbers are not decimal") from exc
        if expect_slices is None:
            expect_slices = tot
        elif tot != expect_slices:
            raise CodecError("blocks disagree on the slice count")
        kind, rest = body.split("/", 1)
        if kind == "H":
            size_hex, crc_hex = rest.split("/")
            this_size = int(size_hex, 16)
            if size is None:
                size = this_size
                expect_crc = crc_hex
            elif this_size != size or crc_hex != expect_crc:
                raise CodecError("header blocks disagree")
        elif kind == "F":
            off_hex, data_hex = rest.split("/", 1)
            off = int(off_hex, 16)
            if len(data_hex) % 2:
                raise CodecError("hex payload has an odd length")
            raw = bytes.fromhex(data_hex)
            previous = chunks.get(off)
            if previous is not None and previous != raw:
                raise CodecError(f"conflicting data at offset {off:04X}")
            chunks[off] = raw
            if blk < 1:
                raise CodecError("data block number must start at 001")
        else:
            raise CodecError(f"unknown block kind {kind}")
    if size is None or not expect_crc:
        raise CodecError("uplink file has no header block")
    if expect_slices != len(chunks):
        raise CodecError(f"expected {expect_slices} data slices, found {len(chunks)}")
    image = bytearray(size)
    covered = bytearray(size)
    for off, raw in chunks.items():
        if off + len(raw) > size:
            raise CodecError(f"slice at {off:04X} runs past the declared file size")
        for i, byte in enumerate(raw):
            if covered[off + i]:
                raise CodecError(f"overlapping slice at offset {off + i}")
            covered[off + i] = 1
            image[off + i] = byte
    if not all(covered):
        missing = covered.find(0)
        raise CodecError(f"gap in rebuilt image at offset {missing}")
    got = file_crc32(bytes(image))
    if got != expect_crc:
        raise CodecError(f"image CRC mismatch: header {expect_crc}, rebuilt {got}")
    return bytes(image)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="latin1", newline="") as handle:
        return handle.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert an ATC database image to or from a .vhf uplink file")
    sub = parser.add_subparsers(dest="cmd", required=True)
    enc = sub.add_parser("convert", help="bin to .vhf")
    enc.add_argument("src")
    enc.add_argument("dst")
    enc.add_argument("--label", default="C1")
    enc.add_argument("--address", default="#ID#")
    enc.add_argument("--ack", default="ACK", choices=["ACK", "NAK"])
    enc.add_argument("--delay", type=int, default=1000)
    dec = sub.add_parser("decode", help=".vhf to bin")
    dec.add_argument("src")
    dec.add_argument("dst")
    info = sub.add_parser("inspect", help="describe a bin")
    info.add_argument("src")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "inspect":
            data = open(args.src, "rb").read()
            seen = inspect_bin(data)
            print(f"size={seen.size} crc32={seen.file_crc32} recognized={seen.recognized}")
            if seen.recognized:
                print(f"part={seen.part} id={seen.db_id} centres={len(seen.centres)} slots={seen.slots}")
            return 0
        if args.cmd == "convert":
            data = open(args.src, "rb").read()
            result = encode_vhf(data, label=args.label, address=args.address, ack=args.ack, delay_ms=args.delay)
            with open(args.dst, "w", encoding="ascii", newline="") as handle:
                handle.write(result.text)
            print(
                f"wrote {args.dst}: {result.air_blocks} blocks, {result.messages} messages, "
                f"session {result.session}, crc {result.file_crc32}"
            )
            for note in result.warnings:
                print(f"warning: {note}")
            return 0
        image = decode_vhf(_read_text(args.src))
        with open(args.dst, "wb") as handle:
            handle.write(image)
        print(f"wrote {args.dst}: {len(image)} bytes, crc {file_crc32(image)}")
        return 0
    except (OSError, CodecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
