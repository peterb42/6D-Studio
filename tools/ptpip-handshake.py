#!/usr/bin/env python3
"""PTP/IP-Handshake-Test für die Canon EOS 6D – mit langen Timeouts.

libgphoto2 wartet nur ~2-3 s auf die Antwort der Kamera. Dieses Skript wartet
bis zu 90 s (Zeit zum Bestätigen an der Kamera) und protokolliert genau, was
die Kamera antwortet. Es verwendet dieselbe GUID wie gphoto2 (~/.gphoto/settings).

Aufruf: ptpip-handshake.py [kamera-ip]
"""
import os
import socket
import struct
import subprocess
import sys
import time

IP = sys.argv[1] if len(sys.argv) > 1 else "172.20.10.3"
PORT = 15740
SETTINGS = os.path.expanduser("~/.gphoto/settings")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_guid():
    try:
        with open(SETTINGS) as f:
            for line in f:
                if line.startswith("ptp2_ip=guid="):
                    return bytes.fromhex(line.strip().split("=", 2)[2].replace(":", ""))
    except FileNotFoundError:
        pass
    sys.exit("Keine ptp2_ip-GUID in ~/.gphoto/settings")


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Kamera hat die Verbindung geschlossen")
        buf += chunk
    return buf


def recv_packet(s, timeout):
    s.settimeout(timeout)
    length, ptype = struct.unpack("<II", recv_exact(s, 8))
    payload = recv_exact(s, length - 8) if length > 8 else b""
    return ptype, payload


def ping(ip):
    return subprocess.run(["ping", "-c", "1", "-t", "1", ip],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def operation(sock, opcode, tid, *params):
    body = struct.pack("<IHI", 1, opcode, tid) + b"".join(struct.pack("<I", p) for p in params)
    sock.sendall(struct.pack("<II", 8 + len(body), 6) + body)
    while True:
        ptype, payload = recv_packet(sock, 30)
        if ptype == 7:  # OperationResponse
            code, rtid = struct.unpack("<HI", payload[:6])
            return code
        log(f"  (Paket Typ {ptype} übersprungen, {len(payload)} Bytes)")


def main():
    guid = load_guid()
    name = socket.gethostname().split(".")[0]
    log("GUID", guid.hex(":"), "| Rechnername", name)

    log("Warte, bis die Kamera", IP, "auf Ping antwortet (max. 20 min) …")
    t0 = time.time()
    while not ping(IP):
        if time.time() - t0 > 1200:
            log("Kamera nicht erreichbar – Abbruch.")
            return 1
        time.sleep(1)
    log("Kamera antwortet auf Ping.")

    # Port 15740 erst öffnen, wenn die Kamera bereit ist; „refused“ ist harmlos.
    t0 = time.time()
    while True:
        try:
            cmd = socket.create_connection((IP, PORT), timeout=5)
            break
        except OSError as e:
            if time.time() - t0 > 600:
                log("Port 15740 öffnet nicht:", e)
                return 1
            time.sleep(1)

    log("TCP verbunden – sende InitCommandRequest, warte bis 90 s auf Antwort (an der Kamera ggf. OK drücken) …")
    body = guid + (name + "\0").encode("utf-16-le") + struct.pack("<HH", 0, 1)
    cmd.sendall(struct.pack("<II", 8 + len(body), 1) + body)
    try:
        ptype, payload = recv_packet(cmd, 90)
    except socket.timeout:
        log("❌ Keine Antwort in 90 s.")
        return 2
    except ConnectionError as e:
        log("❌", e)
        return 2

    if ptype == 5:
        reason = struct.unpack("<I", payload[:4])[0] if len(payload) >= 4 else None
        log("❌ InitFail von der Kamera, Grund:", hex(reason) if reason is not None else "?")
        return 3
    if ptype != 2:
        log("❌ Unerwartete Antwort Typ", ptype, payload.hex())
        return 4

    conn = struct.unpack("<I", payload[:4])[0]
    cam_guid = payload[4:20]
    cam_name = payload[20:-4].decode("utf-16-le", "replace").rstrip("\0")
    log(f"✅ InitCommandAck – Kamera „{cam_name}“, Verbindungs-ID {conn}, Kamera-GUID {cam_guid.hex(':')}")

    evt = socket.create_connection((IP, PORT), timeout=10)
    evt.sendall(struct.pack("<III", 12, 3, conn))
    ptype, _ = recv_packet(evt, 30)
    log("Event-Kanal:", "✅ InitEventAck" if ptype == 4 else f"❌ Typ {ptype}")

    code = operation(cmd, 0x1002, 0, 1)  # OpenSession(1)
    log(f"OpenSession: 0x{code:04x}", "✅" if code == 0x2001 else "❌")
    if code == 0x2001:
        code = operation(cmd, 0x1003, 1)  # CloseSession
        log(f"CloseSession: 0x{code:04x}")
    cmd.close()
    evt.close()
    log("Fertig – Handshake mit dieser GUID funktioniert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
