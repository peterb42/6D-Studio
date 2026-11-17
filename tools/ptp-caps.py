#!/usr/bin/env python3
"""Über den Vermittler: unterstützte Canon-Operationen und aktuelle Eigenschaften auslesen."""
import socket
import struct

HOST = ("127.0.0.1", 15740)


def recv_exact(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("zu")
        b += c
    return b


def recv_packet(s, t=20):
    s.settimeout(t)
    ln, ty = struct.unpack("<II", recv_exact(s, 8))
    return ty, recv_exact(s, ln - 8) if ln > 8 else b""


def op(s, code, tid, *params):
    body = struct.pack("<IHI", 1, code, tid) + b"".join(struct.pack("<I", p) for p in params)
    s.sendall(struct.pack("<II", 8 + len(body), 6) + body)
    data = b""
    while True:
        ty, pl = recv_packet(s)
        if ty == 9:
            continue
        if ty in (10, 12):
            data += pl[4:]
            continue
        if ty == 7:
            return struct.unpack_from("<H", pl)[0], data


def ptp_string(d, o):
    n = d[o]
    s = d[o + 1:o + 1 + 2 * n].decode("utf-16-le").rstrip("\0")
    return s, o + 1 + 2 * n


def u16_array(d, o):
    n = struct.unpack_from("<I", d, o)[0]
    return list(struct.unpack_from("<%dH" % n, d, o + 4)), o + 4 + 2 * n


c = socket.create_connection(HOST, timeout=5)
body = b"\x22" * 16 + "caps\0".encode("utf-16-le") + struct.pack("<HH", 0, 1)
c.sendall(struct.pack("<II", 8 + len(body), 1) + body)
ty, pl = recv_packet(c)
conn = struct.unpack_from("<I", pl)[0]
e = socket.create_connection(HOST, timeout=5)
e.sendall(struct.pack("<III", 12, 3, conn))
recv_packet(e)

tid = 1
rc, d = op(c, 0x1001, tid); tid += 1
o = 8
_, o = ptp_string(d, o)          # VendorExtensionDesc
o += 2                           # FunctionalMode
ops, o = u16_array(d, o)
evs, o = u16_array(d, o)
props, o = u16_array(d, o)
interesting = {0x910F: "RemoteRelease", 0x9125: "BulbStart", 0x9126: "BulbEnd", 0x9128: "RemoteReleaseOn",
               0x9129: "RemoteReleaseOff", 0x9153: "GetViewFinderData", 0x9155: "DriveLens", 0x9158: "Zoom",
               0x9159: "ZoomPosition", 0x9110: "SetDevicePropValueEx", 0x9116: "GetEvent", 0x9117: "TransferComplete",
               0x911A: "PCHDDCapacity", 0x9160: "DoAf", 0x9154: "DoAf(alt)", 0x9127: "SetUILock", 0x9128: "RROn"}
print("GetDeviceInfo:", hex(rc), "| Ops gesamt:", len(ops))
print("Canon-Ops:", " ".join("%04x" % x for x in sorted(ops) if x >= 0x9100))
for k, v in sorted(interesting.items()):
    print("  %04x %-22s %s" % (k, v, "JA" if k in ops else "-"))

rc, _ = op(c, 0x9115, tid, 1); tid += 1
print("SetEventMode:", hex(rc))
rc, _ = op(c, 0x9114, tid, 1); tid += 1
print("SetRemoteMode:", hex(rc))
WANT = {0xD101: "Aperture", 0xD102: "Shutter", 0xD103: "ISO", 0xD105: "AEMode", 0xD11C: "CaptureDest",
        0xD1B0: "EVFOutput", 0xD1B1: "EVFMode", 0xD111: "Battery", 0xD1B3: "EVF?", 0xD1B4: "EVF?", 0xD1B8: "EVF?"}
types = {}
for rnd in range(3):
    rc, d = op(c, 0x9116, tid); tid += 1
    o = 0
    while o + 8 <= len(d):
        size, typ = struct.unpack_from("<II", d, o)
        if size < 8:
            break
        types[typ] = types.get(typ, 0) + 1
        if typ == 0xC189 and size >= 16:
            prop, val = struct.unpack_from("<II", d, o + 8)
            if prop in WANT:
                print("  Wert   %04x %-11s = 0x%x" % (prop, WANT[prop], val))
        elif typ == 0xC18A and size >= 20:
            prop, dt, cnt = struct.unpack_from("<III", d, o + 8)
            if prop in WANT:
                vals = [struct.unpack_from("<I", d, o + 20 + 4 * i)[0] for i in range(min(cnt, (size - 20) // 4))]
                print("  Liste  %04x %-11s typ %d n=%d: %s" % (prop, WANT[prop], dt, cnt, " ".join("%x" % v for v in vals)))
        o += size
    print("GetEvent %d: rc %s, %d Bytes" % (rnd, hex(rc), len(d)))
print("Ereignistypen:", {hex(k): v for k, v in types.items()})
