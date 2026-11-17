#!/usr/bin/env python3
"""PTP/IP-Vermittler für die Canon EOS 6D.

Eigenheiten der 6D, die gphoto2 allein nicht schafft:
  * Die PTP/IP-Anmeldung dauert ~4 s – libgphoto2 wartet nur 2,5 s.
  * Wird die TCP-Verbindung oder die PTP-Session geschlossen, beendet die 6D die
    Fernsteuerung bzw. lehnt danach alle Befehle ab.
  * Nach ~30 s ohne Aktivität trennt die 6D.

Lösung: Der Vermittler meldet sich einmal an, öffnet EINE PTP-Session und hält sie
offen (Wachhalte-Abfrage alle 5 s). gphoto2 verbindet sich mit 127.0.0.1:15740.
OpenSession/CloseSession von gphoto2 beantwortet der Vermittler selbst, alle anderen
Befehle reicht er durch und rechnet die Transaktionsnummern um.

Aufruf: 6d-ptpip-proxy [kamera-ip]
"""
import os
import select
import socket
import struct
import subprocess
import sys
import time

CAM_IP = sys.argv[1] if len(sys.argv) > 1 else "172.20.10.3"
PORT = 15740
LISTEN = ("127.0.0.1", PORT)
SETTINGS = os.path.expanduser("~/.gphoto/settings")
KEEPALIVE_S = 2

INIT_CMD_REQ, INIT_CMD_ACK, INIT_EVT_REQ, INIT_EVT_ACK, INIT_FAIL = 1, 2, 3, 4, 5
OP_REQ, OP_RESP, EVENT, START_DATA, DATA, CANCEL, END_DATA = 6, 7, 8, 9, 10, 11, 12
PROBE_REQ, PROBE_RESP = 13, 14
OPEN_SESSION, CLOSE_SESSION, GET_DEVICE_INFO = 0x1002, 0x1003, 0x1001
EOS_SET_REMOTE_MODE, EOS_SET_EVENT_MODE, EOS_GET_EVENT = 0x9114, 0x9115, 0x9116
# Zustand wie bei EOS Utility herstellen: Fernsteuer- und Ereignismodus an.
EOS_REMOTE_ON = [(EOS_SET_REMOTE_MODE, (1,)), (EOS_SET_EVENT_MODE, (1,))]
RC_OK, RC_SESSION_OPEN = 0x2001, 0x201E
PROBE_RESP_PKT = struct.pack("<II", 8, PROBE_RESP)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_guid():
    with open(SETTINGS) as f:
        for line in f:
            if line.startswith("ptp2_ip=guid="):
                return bytes.fromhex(line.strip().split("=", 2)[2].replace(":", ""))
    sys.exit("Keine ptp2_ip-GUID in ~/.gphoto/settings")


def ptype(pkt):
    return struct.unpack_from("<I", pkt, 4)[0]


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Gegenstelle hat die Verbindung geschlossen")
        buf += chunk
    return buf


def recv_packet(s, timeout=None):
    s.settimeout(timeout)
    hdr = recv_exact(s, 8)
    length = struct.unpack_from("<I", hdr)[0]
    pkt = hdr + (recv_exact(s, length - 8) if length > 8 else b"")
    s.settimeout(None)
    return pkt


class Reader:
    """Sammelt Bytes eines Sockets und liefert vollständige PTP/IP-Pakete."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def read(self):
        data = self.sock.recv(1 << 20)
        if not data:
            return None
        self.buf += data
        out = []
        while len(self.buf) >= 8:
            length = struct.unpack_from("<I", self.buf)[0]
            if length < 8:
                raise ValueError("ungültige Paketlänge")
            if len(self.buf) < length:
                break
            out.append(bytearray(self.buf[:length]))
            del self.buf[:length]
        return out


def ping(ip):
    return subprocess.run(["ping", "-c", "1", "-t", "1", ip],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


class Proxy:
    def __init__(self):
        self.cam_cmd = self.cam_evt = None
        self.cli_cmd = self.cli_evt = None
        self.cmd_ack = self.evt_ack = None
        self.tid = 0
        self.inflight = None      # {'internal': bool, 'cam': tid, 'cli': tid}
        self.queue = []           # Client-Pakete, die auf das Ende einer eigenen Abfrage warten
        self.internal_ops = []    # eigene Befehle (code, params), die noch gesendet werden sollen
        self.last_cam = time.time()
        self.keepalives = 0

    # ---------- Kamera ----------
    def next_tid(self):
        self.tid += 1
        return self.tid

    def login(self, guid, name):
        log("Warte auf die Kamera", CAM_IP, "…")
        while not ping(CAM_IP):
            time.sleep(1)
        while True:
            try:
                cmd = socket.create_connection((CAM_IP, PORT), timeout=5)
                break
            except OSError:
                time.sleep(1)
        log("Melde mich bei der Kamera an (bis 90 s, ggf. an der Kamera OK drücken) …")
        body = guid + (name + "\0").encode("utf-16-le") + struct.pack("<HH", 0, 1)
        cmd.sendall(struct.pack("<II", 8 + len(body), INIT_CMD_REQ) + body)
        ack = recv_packet(cmd, 90)
        if ptype(ack) == INIT_FAIL:
            raise RuntimeError("Kamera lehnt ab (InitFail) – Kopplung an der Kamera neu starten")
        if ptype(ack) != INIT_CMD_ACK:
            raise RuntimeError("Unerwartete Antwort Typ %d" % ptype(ack))
        conn_id = struct.unpack_from("<I", ack, 8)[0]
        evt = socket.create_connection((CAM_IP, PORT), timeout=10)
        evt.sendall(struct.pack("<III", 12, INIT_EVT_REQ, conn_id))
        evt_ack = recv_packet(evt, 30)
        if ptype(evt_ack) != INIT_EVT_ACK:
            raise RuntimeError("Event-Kanal abgelehnt (Typ %d)" % ptype(evt_ack))

        # Eine PTP-Session öffnen und dauerhaft offen halten.
        cmd.sendall(struct.pack("<IIIHII", 22, OP_REQ, 1, OPEN_SESSION, 0, 1))
        while True:
            pkt = recv_packet(cmd, 30)
            if ptype(pkt) == OP_RESP:
                rc = struct.unpack_from("<H", pkt, 8)[0]
                break
        if rc not in (RC_OK, RC_SESSION_OPEN):
            raise RuntimeError("OpenSession abgelehnt: 0x%04x" % rc)

        for s in (cmd, evt):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            s.settimeout(None)
        self.cam_cmd, self.cam_evt = cmd, evt
        self.r_cam_cmd, self.r_cam_evt = Reader(cmd), Reader(evt)
        self.cmd_ack, self.evt_ack = ack, evt_ack
        self.tid = 0
        self.inflight = None
        self.queue = []
        self.internal_ops = list(EOS_REMOTE_ON)
        self.last_cam = time.time()
        log("✅ Kamera verbunden, Session offen – gphoto2 kann jetzt ptpip:127.0.0.1 nutzen.")

    def close_camera(self):
        for s in (self.cam_cmd, self.cam_evt):
            try:
                s.close()
            except OSError:
                pass
        self.drop_client("cli_cmd", quiet=True)
        self.drop_client("cli_evt", quiet=True)

    def send_cam(self, pkt):
        self.cam_cmd.sendall(pkt)
        self.last_cam = time.time()

    def internal_op(self, code, params=()):
        t = self.next_tid()
        self.inflight = {"internal": True, "cam": t, "cli": None, "code": code}
        body = struct.pack("<IHI", 1, code, t) + b"".join(struct.pack("<I", p) for p in params)
        self.send_cam(struct.pack("<II", 8 + len(body), OP_REQ) + body)

    def idle_work(self):
        """Eigene Befehle senden, wenn gerade nichts unterwegs ist."""
        if self.inflight:
            return
        if self.internal_ops:
            code, params = self.internal_ops.pop(0)
            self.internal_op(code, params)
        elif time.time() - self.last_cam > KEEPALIVE_S:
            self.keepalives += 1
            if self.keepalives % 30 == 0:
                log("Verbindung gehalten (%d Wachhalte-Abfragen)" % self.keepalives)
            self.internal_op(EOS_GET_EVENT)

    # ---------- Pakete von gphoto2 ----------
    def on_client_cmd(self, pkt):
        if not self.cli_cmd:
            return
        if self.inflight and self.inflight["internal"]:
            self.queue.append(pkt)
            return
        ty = ptype(pkt)
        if ty == OP_REQ:
            code, ctid = struct.unpack_from("<HI", pkt, 12)
            if code in (OPEN_SESSION, CLOSE_SESSION):
                self.cli_cmd.sendall(struct.pack("<IIHI", 14, OP_RESP, RC_OK, ctid))
                return
            t = self.next_tid()
            self.inflight = {"internal": False, "cam": t, "cli": ctid}
            struct.pack_into("<I", pkt, 14, t)
            self.send_cam(pkt)
        elif ty in (START_DATA, DATA, END_DATA, CANCEL):
            if self.inflight:
                struct.pack_into("<I", pkt, 8, self.inflight["cam"])
            self.send_cam(pkt)
        elif ty == PROBE_REQ:
            self.cli_cmd.sendall(PROBE_RESP_PKT)
        elif ty != PROBE_RESP:
            self.send_cam(pkt)

    # ---------- Pakete von der Kamera ----------
    def on_cam_cmd(self, pkt):
        self.last_cam = time.time()
        ty = ptype(pkt)
        if ty == PROBE_REQ:
            self.cam_cmd.sendall(PROBE_RESP_PKT)
            return
        inf = self.inflight
        if ty in (OP_RESP, START_DATA, DATA, END_DATA) and inf:
            off = 10 if ty == OP_RESP else 8
            if struct.unpack_from("<I", pkt, off)[0] == inf["cam"]:
                if ty == OP_RESP:
                    self.inflight = None
                if inf["internal"]:
                    if ty == OP_RESP:
                        rc = struct.unpack_from("<H", pkt, 8)[0]
                        if rc != RC_OK:
                            log("Eigener Befehl 0x%04x: Antwort 0x%04x" % (inf["code"], rc))
                        queued, self.queue = self.queue, []
                        for q in queued:
                            self.on_client_cmd(q)
                    return
                struct.pack_into("<I", pkt, off, inf["cli"])
        if self.cli_cmd:
            try:
                self.cli_cmd.sendall(pkt)
            except OSError:
                pass

    def on_cam_evt(self, pkt):
        self.last_cam = time.time()
        ty = ptype(pkt)
        if ty == PROBE_REQ:
            self.cam_evt.sendall(PROBE_RESP_PKT)
        elif ty == EVENT and self.cli_evt:
            try:
                self.cli_evt.sendall(pkt)
            except OSError:
                pass

    def drop_client(self, which, quiet=False):
        s = getattr(self, which)
        if not s:
            return
        try:
            s.close()
        except OSError:
            pass
        setattr(self, which, None)
        if which == "cli_cmd" and not quiet:
            log("gphoto2 getrennt – Verbindung zur Kamera bleibt offen.")
            # gphoto2 schaltet beim Beenden den Fernsteuermodus ab – wieder einschalten.
            self.internal_ops.extend(EOS_REMOTE_ON)

    # ---------- Hauptschleife ----------
    def run(self, lst):
        pending = []
        readers = {}

        while True:
            socks = [lst, self.cam_cmd, self.cam_evt] + pending + \
                    [s for s in (self.cli_cmd, self.cli_evt) if s]
            ready, _, _ = select.select(socks, [], [], 0.5)

            for s in ready:
                if s is lst:
                    c, _ = lst.accept()
                    pending.append(c)
                elif s in pending:
                    pending.remove(s)
                    try:
                        pkt = recv_packet(s, 5)
                    except Exception:
                        s.close()
                        continue
                    if ptype(pkt) == INIT_CMD_REQ:
                        self.drop_client("cli_cmd")
                        self.cli_cmd = s
                        readers[s] = Reader(s)
                        s.sendall(self.cmd_ack)
                        log("gphoto2 verbunden")
                    elif ptype(pkt) == INIT_EVT_REQ:
                        self.drop_client("cli_evt")
                        self.cli_evt = s
                        readers[s] = Reader(s)
                        s.sendall(self.evt_ack)
                    else:
                        s.close()
                elif s is self.cam_cmd or s is self.cam_evt:
                    reader = self.r_cam_cmd if s is self.cam_cmd else self.r_cam_evt
                    pkts = reader.read()
                    if pkts is None:
                        log("❌ Kamera hat die Verbindung beendet.")
                        for p in pending:
                            p.close()
                        return
                    for p in pkts:
                        (self.on_cam_cmd if s is self.cam_cmd else self.on_cam_evt)(p)
                elif s is self.cli_cmd or s is self.cli_evt:
                    which = "cli_cmd" if s is self.cli_cmd else "cli_evt"
                    try:
                        pkts = readers[s].read()
                    except (OSError, ValueError):
                        pkts = None
                    if pkts is None:
                        readers.pop(s, None)
                        self.drop_client(which)
                        continue
                    if which == "cli_cmd":
                        for p in pkts:
                            self.on_client_cmd(p)
                    else:
                        for p in pkts:
                            if ptype(p) == PROBE_REQ:
                                s.sendall(PROBE_RESP_PKT)

            self.idle_work()


def main():
    guid = load_guid()
    name = socket.gethostname().split(".")[0]
    lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lst.bind(LISTEN)
    lst.listen(4)
    p = Proxy()
    while True:
        # Trennt die Kamera, einfach neu anmelden – an der Kamera dann die
        # Fernsteuerung (EOS Utility) wieder starten.
        try:
            p.login(guid, name)
        except (OSError, RuntimeError, ConnectionError) as e:
            log("❌ Anmeldung fehlgeschlagen:", e, "– neuer Versuch in 5 s")
            time.sleep(5)
            continue
        p.run(lst)
        p.close_camera()
        log("Warte auf erneute Verbindung der Kamera …")
        time.sleep(3)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        log("❌", e)
        sys.exit(1)
