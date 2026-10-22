#!/usr/bin/env python3
"""btsnoop HCI log inspector for the Huawei Band 6 LPv2 investigation.

Reads a classic `btsnoop_hci.log` (magic "btsnoop\\0", DLT 1002) and reports:
  - record counts, ATT opcode histogram,
  - every LPv2 candidate frame found in ATT writes/notifications (annotated:
    svc, cmd, TLV tags, CRC valid?, result code from tag 127 when present),
  - which handles carried LPv2 (FE01/FE02 channel identification).

Annotation only: this tool never interprets any payload as a health
measurement, and never outputs "readings". Arbitrary/encrypted bodies are
printed as hex with an "opaque" label. (Task rule: nothing is claimed as a
genuine reading unless decoded AND cross-checked on the band display.)

Usage: python btsnoop_inspect.py <btsnoop_hci.log> [--max-frames N]
"""
import struct
import sys
import collections

MAGIC = b"btsnoop\0"

def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
        crc &= 0xFFFF
    return crc

def hexs(b: bytes, n: int = 40) -> str:
    return " ".join("%02x" % x for x in b[:n])

# ---------------------------------------------------------------- btsnoop

def parse(path):
    raw = open(path, "rb").read()
    if raw[:8] != MAGIC:
        raise SystemExit(f"not a btsnoop file: magic {raw[:8].hex()}")
    version, dlt = struct.unpack(">II", raw[8:16])
    print(f"format: version={version} DLT={dlt} (1002=HCI UART H4)")
    recs = []
    off = 16
    idx = 0
    while off + 24 <= len(raw):
        orig, incl, flags, drops, ts = struct.unpack(
            ">IIIIq", raw[off:off + 24])
        off += 24
        data = raw[off:off + incl]
        off += incl
        if incl > orig:
            break
        recs.append((idx, flags, ts, data))
        idx += 1
    return recs

def reassemble_acl(recs):
    """Return list of (ts, direction, handle, l2cap_start?, payload bytearray).

    Direction per record flags bit0 (0 = host->controller "sent",
    1 = controller->host "received"). PB flag is encoded in record flags
    bits 14/15 per btsnoop spec; handle/len from the HCI ACL header.
    """
    buffers = {}  # handle -> list of bytes
    out = []
    for idx, flags, ts, data in recs:
        if not data or data[0] != 0x02:
            continue
        if len(data) < 5:
            continue
        hf = struct.unpack("<H", data[1:3])[0]
        hlen = struct.unpack("<H", data[3:5])[0]
        handle = hf & 0x0FFF
        payload = data[5:5 + hlen]
        pb = (hf >> 12) & 0x3
        direction = "rx" if (flags & 1) else "tx"
        buf = buffers.setdefault(handle, bytearray())
        if pb in (0, 2) and len(payload) >= 4:      # start of L2CAP PDU
            if buf:
                out.append((ts, direction, handle, bytes(buf)))
                buffers[handle] = bytearray()
            l2len, cid = struct.unpack("<HH", payload[:4])
            buf = buffers[handle]
            buf.extend(payload)
        elif pb == 1:                               # continuation
            buf.extend(payload)
        elif pb == 3:                               # complete ACL (no L2CAP)
            out.append((ts, direction, handle, bytes(payload)))
    for handle, buf in buffers.items():
        if buf:
            out.append((None, "?", handle, bytes(buf)))
    return out

def l2cap_entries(acl_pdus):
    """Split reassembled buffers into (ts, dir, handle, cid, body) entries."""
    entries = []
    partial = {}
    for ts, direction, handle, buf in acl_pdus:
        b = partial.pop(handle, None)
        if b is not None:
            buf = b + buf
        while len(buf) >= 4:
            l2len, cid = struct.unpack("<HH", buf[:4])
            if len(buf) < 4 + l2len:
                partial[handle] = buf
                break
            body = buf[4:4 + l2len]
            entries.append((ts, direction, handle, cid, body))
            buf = buf[4 + l2len:]
    return entries

# ---------------------------------------------------------------- LPv2

def varint(b, i, until):
    value = 0
    n = 0
    while i < until and n < 4:
        v = b[i]
        value = (value << 7) | (v & 0x7F)
        n += 1
        i += 1
        if not (v & 0x80):
            return value, n
    return None, 0

def classify_lpv2(value: bytes):
    """(summary, crc_ok) for one candidate frame; annotation only."""
    if len(value) < 3 or value[0] != 0x5A:
        return None
    if value[3] != 0x00:
        return (f"lpv2-SLICED len={value[1]<<8|value[2]}", False)
    body_len = ((value[1] << 8) | value[2]) - 1
    need = 4 + body_len + 2
    if len(value) < need:
        return (f"lpv2-incomplete (want {need}, have {len(value)}) "
                f"hex={hexs(value)}", False)
    crc_rx = (value[4 + body_len] << 8) | value[5 + body_len]
    ok = crc16_xmodem(value[:4 + body_len]) == crc_rx
    svc, cmd = value[4], value[5]
    tags, tlvs = [], []
    i, until = 6, 4 + body_len
    while i < until and len(tags) < 16:
        tag = value[i]
        tags.append(tag)
        i += 1
        if i >= until:
            break
        vlen, n = varint(value, i, until)
        if vlen is None:
            break
        i += n
        if i + vlen > until:
            break
        tlvs.append((tag, value[i:i + vlen]))
        i += vlen
    summary = f"lpv2 svc={svc} cmd={cmd} tags={tags} crc={'OK' if ok else 'BAD'}"
    codes = [(t, int.from_bytes(v, "big")) for t, v in tlvs if t in (126, 127)]
    for t, v in codes:
        summary += f" | tag{t}={v}"
    return (summary, ok)

# ---------------------------------------------------------------- main

def main():
    path = sys.argv[1]
    max_frames = 100000
    if "--max-frames" in sys.argv:
        max_frames = int(sys.argv[sys.argv.index("--max-frames") + 1])
    recs = parse(path)
    print(f"records: {len(recs)}")
    types = collections.Counter(d[0] for *_, d in recs if d)
    print("HCI packet types:", dict(types))

    acl = reassemble_acl(recs)
    print(f"ACL PDUs reassembled: {len(acl)}")
    entries = l2cap_entries(acl)
    att = collections.Counter()
    lpv2_frames = []
    lpv2_handles = collections.Counter()
    for ts, direction, handle, cid, body in entries:
        if cid != 0x0004 or not body:
            continue
        op = body[0]
        att[op] += 1
        value = body[1:]
        if op in (0x1B, 0x1D, 0x52, 0x42, 0x12):
            sub = value
            if op in (0x1B, 0x1D):
                # notification: handle(2) + value
                sub = value[2:]
            elif op in (0x52, 0x42):
                sub = value[2:]
            c = classify_lpv2(sub)
            if c:
                lpv2_frames.append((ts, direction, handle, op, sub, c[0], c[1]))
                lpv2_handles[(handle, direction)] += 1
    print("ATT opcodes:", {hex(k): v for k, v in att.most_common()})
    print("LPv2-carrying handles:", {
        f"0x{h:04x}({d})": n for (h, d), n in lpv2_handles.most_common()})
    print(f"\n=== LPv2 frames (showing up to {max_frames}) ===")
    for ts, direction, handle, op, sub, summary, ok in lpv2_frames[:max_frames]:
        t = f"{ts}" if ts is None else f"{(ts - TS0) / 1e6:.2f}s"
        print(f"{t:>10}  {direction}  h=0x{handle:04x} op=0x{op:02x}  "
              f"{summary}  [{hexs(sub)}]")
    names = {0x1B: "notify", 0x1D: "indicate", 0x52: "write-cmd",
             0x42: "write-req", 0x12: "write-rsp",
             0x10: "read-by-type-req", 0x11: "read-by-type-rsp",
             0x02: "mtu-req", 0x03: "mtu-rsp", 0x0E: "disconnected"}
    opcount = collections.Counter(
        names.get(op, hex(op)) for *_, op, _s, _x, _o in
        [(None, None, None, o, None, None, None) for o in []])

TS0 = None

if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    if len(data) >= 16 + 24:
        TS0 = struct.unpack(">q", data[16 + 20:16 + 28])[0]
    main()
