#!/usr/bin/env python3
"""Decisive offline crypto test (v3) against the captured Health<->Band 6 session.

Validated inputs:
  - zyv/huawei-lpv2 compute_digest port verified against flatsiedatsie's
    independently published Huawei Band 8 vector (MATCH, see section B).
  - extracts all artifacts directly from the btsnoop HCI capture.

Questions:
  B) port sanity            (independent device vector)
  A) do public DIGEST_SECRETS explain Health's 1/19 tag1 and/or the band's
     1/19 reply? -> if yes, normal-mode auth needs no secret on this band
  C) can the post-auth session AES key be derived from public material only?

Annotation only; never interprets payloads as health readings.
"""
import hashlib
import hmac
import importlib.util

SNOOP = r"C:\Users\Tharz\AppData\Local\Temp\opencode\captures\btsnoop_hci.log"

spec = importlib.util.spec_from_file_location(
    "btsnoop", r"D:\Engineering\s7\minor\tools\btsnoop_inspect.py")
insp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(insp)


def parse_tlv(body: bytes):
    out = []
    i = 0
    n = len(body)
    while i < n and len(out) < 32:
        tag = body[i]
        i += 1
        if i >= n:
            out.append((tag, b""))
            break
        vlen = 0
        while i < n and (body[i] & 0x80):
            vlen = (vlen << 7) | (body[i] & 0x7F)
            i += 1
        vlen = (vlen << 7) | body[i]
        i += 1
        if i + vlen > n:
            break
        out.append((tag, body[i:i + vlen]))
        i += vlen
    return out


def frames():
    recs = insp.parse(SNOOP)
    acl = insp.reassemble_acl(recs)
    entries = insp.l2cap_entries(acl)
    fr = []
    for ts, direction, handle, cid, body in entries:
        if cid != 4 or len(body) < 2:
            continue
        op = body[0]
        val = body[1:]
        sub = val[2:] if op in (0x1B, 0x1D, 0x52, 0x42) else val
        if len(sub) < 6 or sub[0] != 0x5A or sub[3] != 0:
            continue
        body_len = ((sub[1] << 8) | sub[2]) - 1
        if len(sub) < 4 + body_len + 2:
            continue
        payload = sub[4:4 + body_len]
        fr.append((op, payload[0], payload[1], parse_tlv(payload[2:])))
    return fr


# ---- zyv constants (public research) ----------------------------------------
DIGEST_SECRETS = {"v1": "70FB6C24035FDB552F38898AEEDE3F69",
                  "v2": "93ACDEF76ACB09857DBFE5261AABCD78",
                  "v3": "9C2763A9CCE134766DE3FF6118200553"}
MSG_RESPONSE_HEX = "0110"   # zyv: used by BOTH digest_challenge and digest_response
MSG_CHALLENGE_HEX = "0100"
SECRET_KEYS = {
    1: (bytes.fromhex("6F756A796D777134636C763933373879"),
        bytes.fromhex("6231306A676664397937767375646139")),
    2: (bytes.fromhex("555386FC632007AA86493522B86AE25C"),
        bytes.fromhex("33079BC57A886D3CF56137096F228000")),
    3: (bytes.fromhex("555386FC632007AA86493522B86AE25C"),
        bytes.fromhex("33079BC57A886D3CF56137096F228000")),
}
BAND_MAC = "9C:56:36:B1:50:61"


def digest_of(secret_hex: str, complete: bytes) -> bytes:
    """zyv nested HMAC-SHA256: HMAC(HMAC(secret, complete), complete).

    secret_hex here already includes the appended MESSAGE hex text.
    """
    secret = bytes.fromhex(secret_hex)

    def dg(k, m):
        return hmac.new(k, msg=m, digestmod=hashlib.sha256).digest()

    return dg(dg(secret, complete), complete)


def secrets_with_message(v: str, msg_hex: str):
    return DIGEST_SECRETS[v] + msg_hex


def create_secret_key(v: int, mac: str) -> bytes:
    mac_key = (mac.replace(":", "") + "0000").encode()
    left, right = SECRET_KEYS[v]
    mixed = bytes(((l << 4) ^ r) & 0xFF for l, r in zip(left, right))
    mh = hashlib.sha256(mixed).digest()
    final = bytes(((m >> 6) ^ k) & 0xFF for m, k in zip(mh, mac_key))
    return hashlib.sha256(final).digest()[:16]


def aescbc(key, iv, ct):
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return None
    try:
        d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return d.update(ct) + d.finalize()
    except Exception:
        return None


def plausible_tlv(b):
    if not b:
        return False
    i = 0
    for _ in range(2):
        if i >= len(b) or b[i] >= 0x80:
            return False
        i += 1
        vlen = 0
        while i < len(b) and (b[i] & 0x80):
            vlen = (vlen << 7) | (b[i] & 0x7F)
            i += 1
        if i >= len(b):
            return False
        vlen = (vlen << 7) | b[i]
        i += 1
        i += vlen
    return b[0] in (0x01, 0x02, 0x03, 0x05, 0x07, 0x7F, 0x7C)


def main():
    fr = frames()
    print(f"parsed {len(fr)} LPv2 frames")

    def dbg(name, b):
        print(f"{name}: {(b or b'').hex().upper()}")

    # ---- extraction ---------------------------------------------------------
    server_nonce = server18 = None
    for op, s, c, t in fr:
        if s == 1 and c == 1 and op != 0x52:
            d = dict(t)
            v5 = d.get(5)
            if v5 and len(v5) >= 18 and v5[:2] == b"\x00\x01":
                server18 = v5[:18]
                server_nonce = v5[2:18]
    dbg("server_nonce (16B)", server_nonce)

    health_digest = client_nonce = client18 = tag3 = None
    for op, s, c, t in fr:
        if s == 1 and c == 19 and op == 0x52:  # phone -> band
            d = dict(t)
            if len(d.get(1, b"")) == 32:
                health_digest = d[1]
                cn_raw = d.get(2, b"")
                if len(cn_raw) >= 18:
                    client18 = cn_raw[:18]
                    client_nonce = cn_raw[2:18]
                tag3 = d.get(3)
    dbg("health 1/19 tag1", health_digest)
    dbg("client_nonce (16B)", client_nonce)
    print("auth tag3 (phone):", (tag3 or b"").hex())

    band_digest = None
    for op, s, c, t in fr:
        if s == 1 and c == 19 and op != 0x52:
            d = dict(t)
            v1 = d.get(1, b"")
            if len(v1) == 32 and v1 != health_digest:
                band_digest = v1
    dbg("band 1/19 reply", band_digest)

    vals_144 = None
    for op, s, c, t in fr:
        if s == 1 and c == 44 and op != 0x52:
            d = dict(t)
            vv = [x for x in (d.get(1), d.get(2)) if x and len(x) == 16]
            if vv:
                vals_144 = vv
    dbg("band 1/44 vals", (b"".join(vals_144) if vals_144 else None))

    encs = []
    for op, s, c, t in fr:
        if op == 0x52:
            d = dict(t)
            if 125 in d and 126 in d and len(d[125]) == 16 and d[126]:
                encs.append((s, c, d[125], d[126]))
    encs = encs[:3]
    for s, c, ivx, ctx in encs:
        print(f"enc frame svc={s} cmd={c}  IV={ivx.hex().upper()}  "
              f"CT={ctx.hex().upper()}")

    # ---- B) port sanity ------------------------------------------------------
    print("\n== B) PORT SANITY vs flatsiedatsie Band-8 published vector ==")
    fb_server = bytes.fromhex("1128779154299E2BDA490573E3F63E30")
    fb_client = bytes.fromhex("CC49AD67218A109C6056372E9433DC11")
    fb_digest = bytes.fromhex(
        "D4EC14969D0B409365AD1F1822092E67A5668BF9599DD5391058640BE400D8EE")
    got = digest_of(secrets_with_message("v1", MSG_RESPONSE_HEX), fb_server + fb_client)
    print("v1 0110 server+client:", got.hex().upper(),
          "MATCH" if got == fb_digest else "NO MATCH")

    # ---- A) decisive: public constants vs captured digests --------------------
    print("\n== A) PUBLIC-CONSTANT VARIANTS vs CAPTURED 1/19 DIGESTS ==")

    def test_complete(target, target_label, complete_name, complete,
                      extra_secret_hex=None):
        hits = []
        for vname in ("v1", "v2", "v3"):
            for msg_label, msg in (("0110", MSG_RESPONSE_HEX),
                                   ("0100", MSG_CHALLENGE_HEX)):
                d = digest_of(DIGEST_SECRETS[vname] + msg, complete)
                matched = target is not None and d == target
                if matched:
                    hits.append(f"{vname}/{msg_label}")
        return hits

    all_hits = []

    if server_nonce and client_nonce:
        completes = {
            "server+client(16B)": server_nonce + client_nonce,
            "client+server(16B)": client_nonce + server_nonce,
        }
        if server18 and client18:
            completes["s18+c18"] = server18 + client18
            completes["c18+s18"] = client18 + server18
        for t_label, target in (("HEALTH", health_digest), ("BAND", band_digest)):
            if target is None:
                continue
            t_hits = []
            for cname, complete in completes.items():
                for hit in test_complete(target, t_label, cname, complete):
                    t_hits.append(f"{cname}:{hit}")
            print(f"{t_label} digest hits: {t_hits or 'NONE (no public variant)'}")
            all_hits.extend((t_label, h) for h in t_hits)

        if vals_144 and len(vals_144) == 2:
            h144 = vals_144[0] + vals_144[1]
            for t_label, target in (("HEALTH", health_digest), ("BAND", band_digest)):
                t_hits = test_complete(target, t_label, "1/44", h144)
                for hit in t_hits:
                    print(f"{t_label} digest hit with complete=1/44vals: {hit}")
                    all_hits.append((t_label, hit))

    if all_hits:
        print("RESULT: PUBLIC-CONSTANT AUTH CONFIRMED for:",
              all_hits)
    else:
        print("RESULT: no public-constant variant explains either digest — "
              "auth (as executed by Health) is bound to bond-specific secret "
              "material on this firmware")

    # extra sanity: HMAC chain with 18B 'complete' also covered; if nothing
    # above matches and B matched, the secret is the differentiator.

    # ---- C) AES-128-CBC candidates --------------------------------------------
    print("\n== C) AES-128-CBC candidates ==")
    key_cands = {}
    for v in (1, 2, 3):
        key_cands[f"create_secret_key(v{v},BAND_MAC)"] = create_secret_key(
            v, BAND_MAC)
    if server_nonce and client_nonce:
        key_cands["sha256(sn||cn)[:16]"] = hashlib.sha256(
            server_nonce + client_nonce).digest()[:16]
        key_cands["sha256(cn||sn)[:16]"] = hashlib.sha256(
            client_nonce + server_nonce).digest()[:16]
    if health_digest and band_digest:
        key_cands["sha256(hdg||bdg)[:16]"] = hashlib.sha256(
            health_digest + band_digest).digest()[:16]
        key_cands["sha256(bdg||hdg)[:16]"] = hashlib.sha256(
            band_digest + health_digest).digest()[:16]

    n_ok = 0
    for kname, key in key_cands.items():
        for idx, (s, c, ivx, ctx) in enumerate(encs):
            pt = aescbc(key, ivx, ctx[:16])
            if pt is None:
                print("cryptography lib not installed — AES checks skipped")
                break
            ok = plausible_tlv(pt) or all(b <= 0x7F for b in pt)
            if ok:
                n_ok += 1
                print(f"{kname} [frame svc={s} cmd={c}]: {pt.hex().upper()}"
                      "  <== PLAUSIBLE")
    if encs and any(aescbc(list(key_cands.values())[0], encs[0][2],
                           encs[0][3][:16]) is not None for _ in [0]):
        print(f"session-key candidates tried: {len(key_cands)}; plausible "
              f"decodes: {n_ok}")
        if not n_ok:
            print("RESULT: none of the public-material keys decrypt the "
                  "captured session — the session cipher key is bond-secret-"
                  "derived (as designed)")
    elif not encs:
        print("no encrypted frames extracted")


if __name__ == "__main__":
    main()
