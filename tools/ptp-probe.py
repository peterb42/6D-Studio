#!/usr/bin/env python3
"""Diagnose: PTP-Befehle über den lokalen Vermittler (127.0.0.1:15740) schicken und Antworten zeigen."""
import socket
import struct
import sys

HOST = ("127.0.0.1", 15740)


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError("geschlossen")
        buf += c
    return buf


def recv_packet(s, t=15):
    s.settimeout(t)
    ln, ty = struct.unpack("<II", recv_exact(s, 8))
    return ty, recv_exact(s, ln - 8) if ln > 8 else b""


def op(s, code, tid, *params, show=True):
    body = struct.pack("<IHI", 1, code, tid) + b"".join(struct.pack("<I", p) for p in params)
    s.sendall(struct.pack("<II", 8 + len(body), 6) + body)
    data = b""
    while True:
        ty, pl = recv_packet(s)
        if ty == 9:  # StartData
            continue
        if ty in (10, 12):  # Data / EndData
            data += pl[4:]
            continue
        if ty == 7:
            rc, rtid = struct.unpack("<HI", pl[:6])
            rparams = [struct.unpack("<I", pl[6 + 4 * i:10 + 4 * i])[0] for i in range((len(pl) - 6) // 4)]
            if show:
                print(f"op 0x{code:04x} tid {tid} -> 0x{rc:04x} params {[hex(p) for p in rparams]} "
                      f"data {len(data)} B", flush=True)
            return rc, data
        print(f"  Paket Typ {ty} ({len(pl)} B) übersprungen", flush=True)


def main():
    c = socket.create_connection(HOST, timeout=5)
    body = b"\x11" * 16 + "probe\0".encode("utf-16-le") + struct.pack("<HH", 0, 1)
    c.sendall(struct.pack("<II", 8 + len(body), 1) + body)
    ty, pl = recv_packet(c)
    conn = struct.unpack("<I", pl[:4])[0]
    e = socket.create_connection(HOST, timeout=5)
    e.sendall(struct.pack("<III", 12, 3, conn))
    recv_packet(e)
    print("verbunden über Vermittler", flush=True)

    tid = 0
    for name, code, params in [("GetDeviceInfo (ohne Session)", 0x1001, ()),
                               ("OpenSession", 0x1002, (1,)),
                               ("GetDeviceInfo", 0x1001, ())]:
        print("->", name, flush=True)
        rc, data = op(c, code, tid, *params)
        tid += 1
        if code == 0x1002 and rc == 0x201E:
            print("   Session war noch offen – schließe und öffne neu", flush=True)
            op(c, 0x1003, tid)
            op(c, 0x1002, 0, 1)
            tid = 1
    if "--keep" not in sys.argv:
        op(c, 0x1003, tid)


if __name__ == "__main__":
    main()
