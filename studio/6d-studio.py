#!/usr/bin/env python3
"""6D Studio – WLAN-Fernsteuerung der Canon EOS 6D mit Bedienoberfläche im Browser.

Ein Prozess hält die PTP/IP-Verbindung zur Kamera und bietet:
  * Bedienoberfläche   http://127.0.0.1:8066
      Livebild mit 5×/10×-Lupe der Kamera, Fokusantrieb, Schärfe-Anzeige für Sterne,
      Bulb Start/Stopp mit Timer, ISO/Blende/Zeit, automatischer Download.
  * Zugang für gphoto2 über ptpip:127.0.0.1 (wird vom Skript 6d-fernsteuerung genutzt).

Eigenheiten der 6D, die hier abgefangen werden:
  * Die PTP/IP-Anmeldung dauert ~4 s (libgphoto2 wartet nur 2,5 s).
  * Endet eine Session oder TCP-Verbindung, beendet die 6D die Fernsteuerung.
  * Ohne regelmäßige Abfragen trennt die 6D nach ~30 s.

Aufruf: 6d-studio [kamera-ip]
"""
import collections
import json
import math
import os
import queue
import select
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

GPDIR = os.path.expanduser("~/.gphoto")
SETTINGS = os.path.join(GPDIR, "settings")
IPFILE = os.path.join(GPDIR, "6d-wlan-ip")


def _default_ip():
    try:
        with open(IPFILE) as f:
            return f.read().strip() or "172.20.10.3"
    except OSError:
        return "172.20.10.3"


CAM_IP = sys.argv[1] if len(sys.argv) > 1 else _default_ip()
PTPIP_PORT = 15740
WEB_PORT = int(os.environ.get("STUDIO_PORT", "8066"))
OUT = os.path.expanduser(os.environ.get("OUT", "~/Pictures/6D-Tethering"))
PREVIEW_DIR = os.path.join(OUT, ".vorschau")
HERE = os.path.dirname(os.path.realpath(__file__))
INDEX_HTML = os.path.join(HERE, "6d-studio.html")

# ---------------------------------------------------------------- Protokoll-Konstanten
INIT_CMD_REQ, INIT_CMD_ACK, INIT_EVT_REQ, INIT_EVT_ACK, INIT_FAIL = 1, 2, 3, 4, 5
OP_REQ, OP_RESP, EVENT, START_DATA, DATA, CANCEL, END_DATA = 6, 7, 8, 9, 10, 11, 12
PROBE_REQ, PROBE_RESP = 13, 14
PROBE_RESP_PKT = struct.pack("<II", 8, PROBE_RESP)

GET_DEVICE_INFO, OPEN_SESSION, CLOSE_SESSION = 0x1001, 0x1002, 0x1003
GET_OBJECT_INFO, GET_OBJECT = 0x1008, 0x1009
REMOTE_RELEASE, SET_PROP, SET_REMOTE_MODE, SET_EVENT_MODE, GET_EVENT = 0x910F, 0x9110, 0x9114, 0x9115, 0x9116
TRANSFER_COMPLETE, BULB_START, BULB_END, RELEASE_ON, RELEASE_OFF = 0x9117, 0x9125, 0x9126, 0x9128, 0x9129
GET_VF_DATA, DRIVE_LENS, ZOOM, ZOOM_POSITION = 0x9153, 0x9155, 0x9158, 0x9159

RC_OK, RC_NOT_SUPPORTED, RC_BUSY, RC_SESSION_OPEN, RC_NOT_READY = 0x2001, 0x2005, 0x2019, 0x201E, 0xA102
RC_TEXT = {0x2002: "allgemeiner Fehler", 0x2005: "nicht unterstützt", 0x2019: "Kamera beschäftigt",
           0x201C: "ungültiger Wert", 0x200F: "Zugriff verweigert", 0xA102: "Kamera nicht bereit"}

P_APERTURE, P_SHUTTER, P_ISO, P_AEMODE = 0xD101, 0xD102, 0xD103, 0xD105
P_CAPTURE_DEST, P_EVF_OUTPUT, P_EVF_MODE = 0xD11C, 0xD1B0, 0xD1B1
DEST_PC = 4                       # „nur Computer“ – Bilder nicht auf der Karte!
SHUTTER_BULB = 0x0C

E_PROP_VALUE, E_AVAIL_LIST = 0xC189, 0xC18A
E_OBJECT_ADDED = (0xC181, 0xC1A7)          # ObjectAddedEx (6D) / ObjectAddedEx64 (neuere Modelle)
E_REQUEST_TRANSFER = (0xC186, 0xC1A9)      # nur bei Speicherort „Computer“
E_WILL_SHUTDOWN, E_SHUTDOWN_TIMER = 0xC18D, 0xC18E
E_KNOWN = {E_PROP_VALUE, E_AVAIL_LIST, E_WILL_SHUTDOWN, E_SHUTDOWN_TIMER, *E_OBJECT_ADDED, *E_REQUEST_TRANSFER}
KEEP_DEVICE_ON = 0x911D

AE_MODES = {0: "P", 1: "Tv", 2: "Av", 3: "M", 4: "B", 5: "A-DEP", 6: "DEP", 7: "C1", 8: "Lock",
            9: "Auto", 15: "Blitz aus", 16: "C2", 19: "Kreativ-Auto", 20: "Film", 22: "Auto+", 25: "SCN"}

# Annahme für das Koordinatensystem der Lupen-Position (Sensorgröße der 6D).
ZOOM_W, ZOOM_H = 5472, 3648


# ---------------------------------------------------------------- Werte anzeigen
ISO_NOMINAL = [50, 64, 80, 100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600, 2000, 2500,
               3200, 4000, 5000, 6400, 8000, 10000, 12800, 16000, 20000, 25600, 32000, 40000, 51200, 102400]
F_NOMINAL = [1.0, 1.1, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5, 2.8, 3.2, 3.5, 4.0, 4.5, 5.0, 5.6, 6.3, 6.7, 7.1, 8, 9,
             9.5, 10, 11, 13, 14, 16, 18, 19, 20, 22, 25, 27, 29, 32, 36, 40, 45]
T_NOMINAL = [30, 25, 20, 15, 13, 10, 8, 6, 5, 4, 3.2, 3, 2.5, 2, 1.6, 1.5, 1.3, 1, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3] + \
            [1 / x for x in (4, 5, 6, 8, 10, 13, 15, 20, 25, 30, 40, 45, 50, 60, 80, 90, 100, 125, 160, 180, 200,
                             250, 320, 350, 400, 500, 640, 750, 800, 1000, 1250, 1500, 1600, 2000, 2500, 3000,
                             3200, 4000, 5000, 6000, 6400, 8000)]


def _nearest(nominal, x):
    return min(nominal, key=lambda n: abs(math.log(n / x)))


def fmt_iso(v):
    if v is None:
        return "–"
    if v == 0:
        return "Auto"
    if v > 0xA0:
        return "?"
    return str(_nearest(ISO_NOMINAL, 100 * 2 ** ((v - 0x48) / 8)))


def fmt_aperture(v):
    if v is None or v == 0 or v > 0x80:
        return "–"
    return "f/%g" % _nearest(F_NOMINAL, 2 ** ((v - 8) / 16))


def fmt_shutter(v):
    if v is None:
        return "–"
    if v == SHUTTER_BULB:
        return "Bulb"
    if v == 0 or v > 0xA0:
        return "Auto"
    t = _nearest(T_NOMINAL, 2 ** (-(v - 0x38) / 8))
    return ('%g"' % t) if t >= 0.3 else "1/%d" % round(1 / t)


def fmt_duration(s):
    s = int(round(s))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


# ---------------------------------------------------------------- Hilfen
class CameraError(Exception):
    """Fehler, der dem Nutzer angezeigt wird."""


class CameraLost(Exception):
    """Verbindung zur Kamera verloren."""


def rc_text(rc):
    return "%s (0x%04x)" % (RC_TEXT.get(rc, "Fehler"), rc)


def ptype(pkt):
    return struct.unpack_from("<I", pkt, 4)[0]


def recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Gegenstelle hat die Verbindung geschlossen")
        buf += chunk
    return bytes(buf)


def recv_packet(s, timeout=None):
    s.settimeout(timeout)
    hdr = recv_exact(s, 8)
    length = struct.unpack_from("<I", hdr)[0]
    pkt = hdr + (recv_exact(s, length - 8) if length > 8 else b"")
    s.settimeout(None)
    return pkt


def sync_op(sock, tid, code, *params, timeout=30):
    """Befehl direkt senden und Antwort abwarten (nur während der Anmeldung)."""
    body = struct.pack("<IHI", 1, code, tid) + b"".join(struct.pack("<I", p) for p in params)
    sock.sendall(struct.pack("<II", 8 + len(body), OP_REQ) + body)
    data = bytearray()
    while True:
        pkt = recv_packet(sock, timeout)
        ty = ptype(pkt)
        if ty in (DATA, END_DATA):
            data += pkt[12:]
        elif ty == OP_RESP:
            return struct.unpack_from("<H", pkt, 8)[0], bytes(data)
        elif ty == PROBE_REQ:
            sock.sendall(PROBE_RESP_PKT)


def ptp_string(d, o):
    n = d[o]
    return d[o + 1:o + 1 + 2 * n].decode("utf-16-le", "replace").rstrip("\0"), o + 1 + 2 * n


def parse_device_info(d):
    def u16arr(o):
        n = struct.unpack_from("<I", d, o)[0]
        return list(struct.unpack_from("<%dH" % n, d, o + 4)), o + 4 + 2 * n
    o = 8
    _, o = ptp_string(d, o)          # VendorExtensionDesc
    o += 2                           # FunctionalMode
    ops, o = u16arr(o)
    for _ in range(4):               # Events, Properties, CaptureFormats, ImageFormats
        _, o = u16arr(o)
    _, o = ptp_string(d, o)          # Hersteller
    model, o = ptp_string(d, o)
    return set(ops), model


def extract_jpeg(data):
    o = 0
    while o + 8 <= len(data):
        ln, ty = struct.unpack_from("<II", data, o)
        if ln < 8:
            break
        if ty == 1:
            return data[o + 8:o + ln]
        o += ln
    i, j = data.find(b"\xff\xd8"), data.rfind(b"\xff\xd9")
    return data[i:j + 2] if i >= 0 and j > i else None


def load_guid():
    with open(SETTINGS) as f:
        for line in f:
            if line.startswith("ptp2_ip=guid="):
                return bytes.fromhex(line.strip().split("=", 2)[2].replace(":", ""))
    sys.exit("Keine ptp2_ip-GUID in ~/.gphoto/settings – zuerst koppeln.")


def ping(ip):
    return subprocess.run(["ping", "-c", "1", "-t", "1", ip],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


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


class Op:
    def __init__(self, code, params=(), data=None, timeout=15, callback=None, name=None):
        self.code, self.params, self.data, self.timeout = code, tuple(params), data, timeout
        self.callback = callback
        self.name = name or "0x%04x" % code
        self.done = threading.Event()
        self.rc, self.rparams, self.result = None, (), b""

    def effective_rc(self):
        """Canon meldet manche Fehler im ersten Antwortparameter."""
        if self.rc == RC_OK and self.rparams and (self.rparams[0] & 0x7000) == 0x2000:
            return self.rparams[0]
        return self.rc


# ---------------------------------------------------------------- Kamera-Steuerung
class Studio:
    def __init__(self):
        self.lock = threading.RLock()
        self.ops = queue.Queue()
        self.wake_r, self.wake_w = socket.socketpair()
        self.logs = collections.deque(maxlen=80)
        self.connected = False
        self.status = "Starte …"
        self.model = ""
        self.supported = set()
        self.props, self.avail = {}, {}
        self.dest_checked = False
        self.seen_events = set()
        self.last_keepon = 0.0
        # Livebild
        self.liveview = False
        self.zoom = 1
        self.zoom_center = [0.5, 0.5]
        self.frame, self.frame_seq, self.frame_time = None, 0, 0.0
        self.frame_cond = threading.Condition()
        self.fps_times = collections.deque(maxlen=20)
        self.lv_error_logged = False
        # Aufnahme
        self.bulb = None
        self.bulb_timer = None
        self.autodownload = True
        self.downloads = queue.Queue()
        self.downloading = None
        self.photos = collections.deque(maxlen=24)
        # Verbindung
        self.cam_cmd = self.cam_evt = None
        self.cli_cmd = self.cli_evt = None
        self.cmd_ack = self.evt_ack = None
        self.tid = 0
        self.inflight = None
        self.client_queue = []
        self.internal_queue = []
        self.last_event = self.last_frame_req = self.last_cam = 0.0
        os.makedirs(PREVIEW_DIR, exist_ok=True)

    # ---------- Protokoll
    def log(self, *a):
        line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
        print(line, flush=True)
        with self.lock:
            self.logs.append(line)

    # ---------- Anmeldung
    def login(self, guid, name):
        with self.lock:
            self.status = "Warte auf die Kamera"
        self.log("Warte auf die Kamera", CAM_IP, "…")
        while not ping(CAM_IP):
            time.sleep(1)
        while True:
            try:
                cmd = socket.create_connection((CAM_IP, PTPIP_PORT), timeout=5)
                break
            except OSError:
                time.sleep(1)
        with self.lock:
            self.status = "Melde mich an"
        self.log("Melde mich bei der Kamera an (bis 90 s, ggf. an der Kamera OK drücken) …")
        body = guid + (name + "\0").encode("utf-16-le") + struct.pack("<HH", 0, 1)
        cmd.sendall(struct.pack("<II", 8 + len(body), INIT_CMD_REQ) + body)
        ack = recv_packet(cmd, 90)
        if ptype(ack) == INIT_FAIL:
            raise CameraLost("Kamera lehnt die Anmeldung ab – an der Kamera die Kopplung neu starten")
        if ptype(ack) != INIT_CMD_ACK:
            raise CameraLost("unerwartete Antwort Typ %d" % ptype(ack))
        conn_id = struct.unpack_from("<I", ack, 8)[0]
        evt = socket.create_connection((CAM_IP, PTPIP_PORT), timeout=10)
        evt.sendall(struct.pack("<III", 12, INIT_EVT_REQ, conn_id))
        evt_ack = recv_packet(evt, 30)
        if ptype(evt_ack) != INIT_EVT_ACK:
            raise CameraLost("Event-Kanal abgelehnt")

        rc, _ = sync_op(cmd, 0, OPEN_SESSION, 1)
        if rc not in (RC_OK, RC_SESSION_OPEN):
            raise CameraLost("OpenSession abgelehnt: " + rc_text(rc))
        rc, info = sync_op(cmd, 1, GET_DEVICE_INFO)
        supported, model = parse_device_info(info) if rc == RC_OK else (set(), "")

        for s in (cmd, evt):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            s.settimeout(None)
        with self.lock:
            self.cam_cmd, self.cam_evt = cmd, evt
            self.r_cam_cmd, self.r_cam_evt = Reader(cmd), Reader(evt)
            self.cmd_ack, self.evt_ack = ack, evt_ack
            self.tid = 1
            self.inflight = None
            self.client_queue = []
            self.supported, self.model = supported, model or "Canon EOS"
            self.props, self.avail = {}, {}
            self.dest_checked = False
            self.internal_queue = self.remote_on_ops()
            if self.liveview:
                self.internal_queue += self.evf_ops(True)
            self.last_cam = time.time()
            self.connected = True
            self.status = "Verbunden"
        names = {BULB_START: "BulbStart", RELEASE_ON: "RemoteReleaseOn", REMOTE_RELEASE: "RemoteRelease",
                 GET_VF_DATA: "Livebild", ZOOM: "Lupe", DRIVE_LENS: "Fokusantrieb", KEEP_DEVICE_ON: "Wachhalten"}
        self.log("Kamera kann:", ", ".join(n for c, n in names.items() if c in supported) or "?")
        self.log("✅ Kamera verbunden (%s), Session offen – Oberfläche: http://127.0.0.1:%d" % (self.model, WEB_PORT))

    def remote_on_ops(self):
        return [Op(SET_REMOTE_MODE, (1,), name="Fernsteuermodus"), Op(SET_EVENT_MODE, (1,), name="Ereignismodus")]

    def evf_ops(self, on):
        if on:
            return [Op(SET_PROP, data=struct.pack("<III", 12, P_EVF_MODE, 1), name="EVF-Modus"),
                    Op(SET_PROP, data=struct.pack("<III", 12, P_EVF_OUTPUT, 2), name="Livebild an")]
        return [Op(SET_PROP, data=struct.pack("<III", 12, P_EVF_OUTPUT, 0), name="Livebild aus")]

    def disconnect(self):
        with self.lock:
            self.connected = False
            for s in (self.cam_cmd, self.cam_evt, self.cli_cmd, self.cli_evt):
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass
            self.cam_cmd = self.cam_evt = self.cli_cmd = self.cli_evt = None
            pending = []
            if self.inflight and self.inflight.get("op"):
                pending.append(self.inflight["op"])
            self.inflight = None
            pending += self.internal_queue
            self.internal_queue = []
            if self.bulb:
                self.log("⚠️ Verbindung während der Belichtung verloren – die Kamera belichtet evtl. weiter.")
            self.bulb = None
            if self.bulb_timer:
                self.bulb_timer.cancel()
        while True:
            try:
                pending.append(self.ops.get_nowait())
            except queue.Empty:
                break
        for op in pending:
            op.done.set()

    # ---------- Befehle aus anderen Threads
    def submit(self, code, params=(), data=None, timeout=15, name=None):
        if not self.connected:
            raise CameraError("Kamera nicht verbunden")
        op = Op(code, params, data, timeout, name=name)
        self.ops.put(op)
        self.wake_w.send(b"x")
        if not op.done.wait(timeout + 10):
            raise CameraError("Zeitüberschreitung bei „%s“" % op.name)
        if op.rc is None:
            raise CameraError("Verbindung zur Kamera unterbrochen")
        return op

    def set_prop(self, code, value, name=None):
        op = self.submit(SET_PROP, data=struct.pack("<III", 12, code, value), name=name or "Einstellung")
        if op.rc != RC_OK:
            raise CameraError("Kamera lehnt „%s“ ab: %s" % (op.name, rc_text(op.rc)))

    # ---------- Kamera-Thread
    def next_tid(self):
        self.tid += 1
        return self.tid

    def send_cam(self, pkt):
        self.cam_cmd.sendall(pkt)
        self.last_cam = time.time()

    def send_op(self, op):
        t = self.next_tid()
        phase = 2 if op.data is not None else 1
        body = struct.pack("<IHI", phase, op.code, t) + b"".join(struct.pack("<I", p) for p in op.params)
        pkts = [struct.pack("<II", 8 + len(body), OP_REQ) + body]
        if op.data is not None:
            pkts.append(struct.pack("<IIIQ", 20, START_DATA, t, len(op.data)))
            pkts.append(struct.pack("<III", 12 + len(op.data), END_DATA, t) + op.data)
        self.inflight = {"internal": True, "cam": t, "op": op, "buf": bytearray(), "total": 0,
                         "sent": time.time(), "timeout": op.timeout}
        self.send_cam(b"".join(pkts))

    def finish(self, op, rc, rparams, data):
        op.rc, op.rparams, op.result = rc, rparams, data
        if op.callback:
            try:
                op.callback(op)
            except Exception as e:      # Auswertung darf die Verbindung nie stören
                self.log("Fehler bei der Auswertung von %s: %r" % (op.name, e))
        elif rc != RC_OK and op.name in ("Fernsteuermodus", "Ereignismodus", "EVF-Modus"):
            pass                        # nicht jede Kamera kennt alles
        op.done.set()

    def on_cam_cmd(self, pkt):
        ty = ptype(pkt)
        if ty == PROBE_REQ:
            self.cam_cmd.sendall(PROBE_RESP_PKT)
            return
        inf = self.inflight
        if inf and ty in (OP_RESP, START_DATA, DATA, END_DATA):
            off = 10 if ty == OP_RESP else 8
            if struct.unpack_from("<I", pkt, off)[0] == inf["cam"]:
                if inf["internal"]:
                    if ty == START_DATA:
                        inf["total"] = struct.unpack_from("<Q", pkt, 12)[0]
                    elif ty in (DATA, END_DATA):
                        inf["buf"] += pkt[12:]
                    else:
                        rc = struct.unpack_from("<H", pkt, 8)[0]
                        n = (len(pkt) - 14) // 4
                        rparams = struct.unpack_from("<%dI" % n, pkt, 14) if n > 0 else ()
                        self.inflight = None
                        self.finish(inf["op"], rc, rparams, bytes(inf["buf"]))
                        queued, self.client_queue = self.client_queue, []
                        for q in queued:
                            self.on_client_cmd(q)
                    return
                struct.pack_into("<I", pkt, off, inf["cli"])
                if ty == OP_RESP:
                    self.inflight = None
        if self.cli_cmd:
            try:
                self.cli_cmd.sendall(pkt)
            except OSError:
                pass

    def on_cam_evt(self, pkt):
        ty = ptype(pkt)
        if ty == PROBE_REQ:
            self.cam_evt.sendall(PROBE_RESP_PKT)
        elif ty == EVENT and self.cli_evt:
            try:
                self.cli_evt.sendall(pkt)
            except OSError:
                pass

    def on_client_cmd(self, pkt):
        """Pakete von gphoto2: Session vortäuschen, Transaktionsnummern umrechnen."""
        if not self.cli_cmd:
            return
        if self.inflight and self.inflight["internal"]:
            self.client_queue.append(pkt)
            return
        ty = ptype(pkt)
        if ty == OP_REQ:
            code, ctid = struct.unpack_from("<HI", pkt, 12)
            if code in (OPEN_SESSION, CLOSE_SESSION):
                self.cli_cmd.sendall(struct.pack("<IIHI", 14, OP_RESP, RC_OK, ctid))
                return
            t = self.next_tid()
            self.inflight = {"internal": False, "cam": t, "cli": ctid, "sent": time.time(), "timeout": 120}
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

    def drop_client(self, which):
        s = getattr(self, which)
        if not s:
            return
        try:
            s.close()
        except OSError:
            pass
        setattr(self, which, None)
        if which == "cli_cmd":
            self.log("gphoto2 getrennt – Verbindung zur Kamera bleibt offen.")
            # gphoto2 schaltet beim Beenden Fernsteuerung/Livebild ab – wiederherstellen.
            self.internal_queue += self.remote_on_ops()
            if self.liveview:
                self.internal_queue += self.evf_ops(True)

    def idle_work(self):
        if self.inflight or not self.cam_cmd:
            return
        if self.internal_queue:
            self.send_op(self.internal_queue.pop(0))
            return
        try:
            self.send_op(self.ops.get_nowait())
            return
        except queue.Empty:
            pass
        now = time.time()
        if KEEP_DEVICE_ON in self.supported and now - self.last_keepon > 60:
            # vorsorglich jede Minute – damit die Abschaltautomatik nie zuschlägt
            self.last_keepon = now
            self.send_op(Op(KEEP_DEVICE_ON, name="Wach halten"))
        elif now - self.last_event > 0.5:
            self.last_event = now
            self.send_op(Op(GET_EVENT, timeout=15, callback=self.on_events, name="Ereignisse"))
        elif self.liveview and not self.bulb and now - self.last_frame_req > 0.06:
            self.last_frame_req = now
            self.send_op(Op(GET_VF_DATA, (0x00100000,), timeout=15, callback=self.on_frame, name="Livebild"))

    def check_watchdog(self):
        inf = self.inflight
        if inf and time.time() - inf["sent"] > inf["timeout"]:
            name = inf["op"].name if inf.get("op") else "gphoto2-Befehl"
            raise CameraLost("Kamera antwortet nicht (%s) – evtl. im Ruhezustand" % name)

    def run(self, lst):
        pending, readers = [], {}
        while True:
            socks = [lst, self.cam_cmd, self.cam_evt, self.wake_r] + pending + \
                    [s for s in (self.cli_cmd, self.cli_evt) if s]
            ready, _, _ = select.select(socks, [], [], 0.05)
            for s in ready:
                if s is self.wake_r:
                    self.wake_r.recv(4096)
                elif s is lst:
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
                        self.log("gphoto2 verbunden")
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
                        for p in pending:
                            p.close()
                        raise CameraLost("Kamera hat die Verbindung beendet.")
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
                    for p in pkts:
                        if which == "cli_cmd":
                            self.on_client_cmd(p)
                        elif ptype(p) == PROBE_REQ:
                            s.sendall(PROBE_RESP_PKT)
            self.check_watchdog()
            self.idle_work()

    def camera_thread(self, guid, name):
        lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        lst.bind(("127.0.0.1", PTPIP_PORT))
        lst.listen(4)
        while True:
            try:
                self.login(guid, name)
                self.run(lst)
            except (CameraLost, ConnectionError, OSError, ValueError) as e:
                self.log("❌", e)
            except Exception as e:
                self.log("❌ Unerwarteter Fehler:", repr(e))
            self.disconnect()
            with self.lock:
                self.status = "Getrennt – an der Kamera „Fernsteuerung (EOS Utility)“ neu starten"
            self.log("Warte auf erneute Verbindung der Kamera …")
            time.sleep(3)

    # ---------- Auswertung von Kameradaten (läuft im Kamera-Thread, muss kurz sein)
    def on_events(self, op):
        if op.rc != RC_OK:
            return
        data, o, new_objects = op.result, 0, []
        with self.lock:
            while o + 8 <= len(data):
                size, typ = struct.unpack_from("<II", data, o)
                if size < 8 or o + size > len(data) or typ == 0:
                    break
                if typ == E_PROP_VALUE and size >= 16:
                    prop, val = struct.unpack_from("<II", data, o + 8)
                    old = self.props.get(prop)
                    self.props[prop] = val
                    if prop == P_AEMODE and old is not None and old != val:
                        self.log("Moduswahlrad: %s" % AE_MODES.get(val, val))
                elif typ == E_AVAIL_LIST and size >= 20:
                    prop, _dtype, cnt = struct.unpack_from("<III", data, o + 8)
                    n = min(cnt, (size - 20) // 4)
                    self.avail[prop] = list(struct.unpack_from("<%dI" % n, data, o + 20)) if n > 0 else []
                elif typ in E_OBJECT_ADDED and size >= 12:
                    new_objects.append((struct.unpack_from("<I", data, o + 8)[0], False))
                elif typ in E_REQUEST_TRANSFER and size >= 12:
                    new_objects.append((struct.unpack_from("<I", data, o + 8)[0], True))
                elif typ == E_WILL_SHUTDOWN:
                    # Abschaltautomatik: wie EOS Utility mit „wach bleiben“ antworten.
                    self.internal_queue.append(Op(KEEP_DEVICE_ON, name="Wach halten"))
                    self.log("Kamera wollte sich abschalten – wach gehalten.")
                elif typ not in E_KNOWN and typ not in self.seen_events:
                    self.seen_events.add(typ)
                    self.log("(Ereignis 0x%04x, %d Bytes)" % (typ, size))
                o += size
            # Bilder sicher auf der Karte speichern (nicht „nur Computer“).
            if not self.dest_checked and P_CAPTURE_DEST in self.props:
                self.dest_checked = True
                if self.props[P_CAPTURE_DEST] == DEST_PC:
                    card = [v for v in self.avail.get(P_CAPTURE_DEST, []) if v != DEST_PC]
                    if card:
                        self.internal_queue.append(
                            Op(SET_PROP, data=struct.pack("<III", 12, P_CAPTURE_DEST, card[0]), name="Speicherort Karte"))
                        self.log("Speicherort auf Speicherkarte gestellt.")
        for handle, transfer in new_objects:
            if transfer or self.autodownload:
                self.downloads.put((handle, transfer))

    def on_frame(self, op):
        if op.rc == RC_OK and op.result:
            jpeg = extract_jpeg(op.result)
            if jpeg:
                now = time.time()
                with self.frame_cond:
                    self.frame, self.frame_time = jpeg, now
                    self.frame_seq += 1
                    self.fps_times.append(now)
                    self.frame_cond.notify_all()
        elif op.rc not in (RC_NOT_READY, RC_BUSY) and not self.lv_error_logged:
            self.lv_error_logged = True
            self.log("Livebild: Kamera antwortet %s" % rc_text(op.rc))

    # ---------- Aktionen der Oberfläche (laufen in HTTP-Threads)
    def is_bulb_mode(self):
        with self.lock:
            return self.props.get(P_AEMODE) == 4 or self.props.get(P_SHUTTER) == SHUTTER_BULB

    def act_set(self, body):
        props = {"iso": (P_ISO, "ISO", fmt_iso), "aperture": (P_APERTURE, "Blende", fmt_aperture),
                 "shutter": (P_SHUTTER, "Belichtungszeit", fmt_shutter)}
        if body.get("prop") not in props:
            raise CameraError("Unbekannte Einstellung")
        code, label, fmt = props[body["prop"]]
        value = int(body["value"])
        allowed = self.avail.get(code)
        if allowed and value not in allowed:
            raise CameraError("%s %s ist im aktuellen Modus nicht möglich" % (label, fmt(value)))
        self.set_prop(code, value, name=label)
        self.log("%s → %s" % (label, fmt(value)))

    def act_liveview(self, body):
        on = bool(body.get("on"))
        if on and GET_VF_DATA not in self.supported:
            raise CameraError("Diese Kamera liefert kein Livebild über WLAN")
        if on:
            try:
                self.set_prop(P_EVF_MODE, 1, name="EVF-Modus")
            except CameraError:
                pass
            self.set_prop(P_EVF_OUTPUT, 2, name="Livebild an")
            with self.lock:
                self.liveview, self.zoom, self.lv_error_logged = True, 1, False
            self.log("Livebild an")
        else:
            with self.lock:
                self.liveview = False
            self.set_prop(P_EVF_OUTPUT, 0, name="Livebild aus")
            self.log("Livebild aus")

    def zoom_xy(self, level):
        w, h = ZOOM_W / level, ZOOM_H / level
        x = clamp(self.zoom_center[0] * ZOOM_W - w / 2, 0, ZOOM_W - w)
        y = clamp(self.zoom_center[1] * ZOOM_H - h / 2, 0, ZOOM_H - h)
        return int(x), int(y)

    def act_zoom(self, body):
        level = int(body.get("level", 1))
        if level not in (1, 5, 10):
            raise CameraError("Lupe nur 1×, 5× oder 10×")
        if not self.liveview:
            raise CameraError("Zuerst das Livebild einschalten")
        if "x" in body and "y" in body:
            self.zoom_center = [clamp(float(body["x"]), 0, 1), clamp(float(body["y"]), 0, 1)]
        if ZOOM_POSITION in self.supported:
            self.submit(ZOOM_POSITION, self.zoom_xy(max(level, 5)), name="Lupen-Position")
        op = self.submit(ZOOM, (level,), name="Lupe")
        if op.rc != RC_OK:
            raise CameraError("Lupe nicht möglich: " + rc_text(op.rc))
        self.zoom = level
        self.log("Lupe %d×" % level)

    def act_zoommove(self, body):
        if body.get("center"):
            self.zoom_center = [0.5, 0.5]
        else:
            step = 0.25 / max(self.zoom, 1)
            self.zoom_center = [clamp(self.zoom_center[0] + float(body.get("dx", 0)) * step, 0, 1),
                                clamp(self.zoom_center[1] + float(body.get("dy", 0)) * step, 0, 1)]
        op = self.submit(ZOOM_POSITION, self.zoom_xy(max(self.zoom, 5)), name="Lupen-Position")
        if op.rc != RC_OK:
            raise CameraError("Lupen-Position nicht möglich: " + rc_text(op.rc))

    def act_focus(self, body):
        step = int(body.get("step", 0))
        if step == 0 or abs(step) > 3:
            raise CameraError("Ungültiger Fokusschritt")
        if not self.liveview:
            raise CameraError("Fokusantrieb geht nur mit eingeschaltetem Livebild")
        code = -step if step < 0 else 0x8000 | step
        op = self.submit(DRIVE_LENS, (code,), name="Fokusantrieb")
        if op.effective_rc() != RC_OK:
            raise CameraError("Fokusantrieb nicht möglich (%s) – Schalter am Objektiv auf AF stellen"
                              % rc_text(op.effective_rc()))

    def act_bulb_start(self, body):
        seconds = max(0, int(float(body.get("seconds") or 0)))
        with self.lock:
            if self.bulb:
                raise CameraError("Es läuft bereits eine Belichtung")
        if self.props and not self.is_bulb_mode():
            raise CameraError("Für Bulb das Moduswahlrad der Kamera auf B stellen")
        if BULB_START in self.supported:
            method, op = "bulb", self.submit(BULB_START, name="Bulb Start", timeout=20)
        elif RELEASE_ON in self.supported:
            method, op = "release", self.submit(RELEASE_ON, (3, 0), name="Bulb Start", timeout=20)
        else:
            raise CameraError("Kamera unterstützt keinen Bulb-Start über WLAN")
        rc = op.effective_rc()
        if rc != RC_OK and method == "bulb" and RELEASE_ON in self.supported:
            method, op = "release", self.submit(RELEASE_ON, (3, 0), name="Bulb Start", timeout=20)
            rc = op.effective_rc()
        if rc != RC_OK:
            raise CameraError("Belichtung startet nicht: %s" % rc_text(rc))
        with self.lock:
            self.bulb = {"start": time.time(), "planned": seconds, "method": method}
            if seconds:
                self.bulb_timer = threading.Timer(seconds, self._bulb_autostop)
                self.bulb_timer.daemon = True
                self.bulb_timer.start()
        self.log("● Bulb-Belichtung gestartet" + (" – Auto-Stopp nach %s" % fmt_duration(seconds) if seconds else ""))

    def _bulb_autostop(self):
        try:
            self.act_bulb_stop({})
        except CameraError as e:
            self.log("❌ Auto-Stopp:", e)

    def act_bulb_stop(self, body):
        with self.lock:
            b = self.bulb
            if not b:
                raise CameraError("Es läuft keine Belichtung")
            if self.bulb_timer:
                self.bulb_timer.cancel()
                self.bulb_timer = None
        if b["method"] == "bulb":
            op = self.submit(BULB_END, name="Bulb Stopp", timeout=30)
        else:
            op = self.submit(RELEASE_OFF, (3,), name="Bulb Stopp", timeout=30)
        rc = op.effective_rc()
        if rc != RC_OK:
            raise CameraError("Stopp fehlgeschlagen: %s – bitte erneut drücken" % rc_text(rc))
        with self.lock:
            self.bulb = None
        self.log("■ Belichtung beendet nach %s" % fmt_duration(time.time() - b["start"]))

    def act_shoot(self, body):
        if self.is_bulb_mode():
            raise CameraError("Im Modus B bitte Bulb Start/Stopp verwenden")
        if REMOTE_RELEASE in self.supported:
            op = self.submit(REMOTE_RELEASE, name="Auslösen", timeout=60)
            rc = op.effective_rc()
            if rc == RC_OK and op.rparams and op.rparams[0] not in (0,):
                raise CameraError("Kamera hat nicht ausgelöst (Code %d) – bei Nacht Objektiv auf MF stellen"
                                  % op.rparams[0])
        else:
            op = self.submit(RELEASE_ON, (3, 0), name="Auslösen", timeout=60)
            rc = op.effective_rc()
            self.submit(RELEASE_OFF, (3,), name="Auslösen loslassen", timeout=60)
        if rc != RC_OK:
            raise CameraError("Auslösen fehlgeschlagen: %s" % rc_text(rc))
        self.log("📷 Ausgelöst")

    def act_autodownload(self, body):
        self.autodownload = bool(body.get("on"))
        self.log("Automatischer Download %s" % ("an" if self.autodownload else "aus"))

    # ---------- Download
    def download_worker(self):
        while True:
            handle, transfer = self.downloads.get()
            try:
                self.download(handle, transfer)
            except CameraError as e:
                self.log("❌ Download:", e)
            except Exception as e:
                self.log("❌ Download:", repr(e))
            finally:
                with self.lock:
                    self.downloading = None

    def download(self, handle, transfer):
        info = self.submit(GET_OBJECT_INFO, (handle,), name="Bildinfo", timeout=30)
        if info.rc != RC_OK or len(info.result) < 53:
            raise CameraError("Bildinfo nicht lesbar: %s" % rc_text(info.rc))
        size = struct.unpack_from("<I", info.result, 8)[0]
        name, _ = ptp_string(info.result, 52)
        name = os.path.basename(name) or "IMG_%08x" % handle
        with self.lock:
            self.downloading = {"name": name, "total": size}
        self.log("⬇︎ Lade %s (%.1f MB) …" % (name, size / 1e6))
        t0 = time.time()
        op = self.submit(GET_OBJECT, (handle,), name="Download " + name, timeout=900)
        if op.rc != RC_OK or not op.result:
            raise CameraError("Download fehlgeschlagen: %s" % rc_text(op.rc))
        if transfer and TRANSFER_COMPLETE in self.supported:
            self.submit(TRANSFER_COMPLETE, (handle,), name="Übertragung fertig")
        stem, ext = os.path.splitext(name)
        path = os.path.join(OUT, name)
        if os.path.exists(path):
            path = os.path.join(OUT, "%s_%s%s" % (stem, time.strftime("%H%M%S"), ext))
        with open(path, "wb") as f:
            f.write(op.result)
        dt = max(time.time() - t0, 0.1)
        self.log("✔︎ Gespeichert: %s (%.1f MB/s)" % (os.path.basename(path), len(op.result) / 1e6 / dt))
        preview = os.path.join(PREVIEW_DIR, os.path.basename(path) + ".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "1800", path, "--out", preview],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.lock:
            self.photos.appendleft({"name": os.path.basename(path), "time": time.strftime("%H:%M:%S"),
                                    "size": len(op.result),
                                    "preview": os.path.basename(preview) if os.path.exists(preview) else None})

    # ---------- Zustand für die Oberfläche
    def snapshot(self):
        with self.lock:
            def prop(code, fmt):
                v = self.props.get(code)
                return {"value": v, "label": fmt(v),
                        "options": [[x, fmt(x)] for x in self.avail.get(code, [])]}
            ae = self.props.get(P_AEMODE)
            inf = self.inflight
            dl = None
            if self.downloading:
                got = len(inf["buf"]) if inf and inf.get("op") and inf["op"].code == GET_OBJECT else 0
                dl = dict(self.downloading, got=got)
            fps = 0.0
            if len(self.fps_times) >= 2 and time.time() - self.fps_times[-1] < 2:
                fps = (len(self.fps_times) - 1) / max(self.fps_times[-1] - self.fps_times[0], 0.001)
            return {
                "connected": self.connected, "status": self.status, "model": self.model, "ip": CAM_IP,
                "mode": AE_MODES.get(ae, "?" if ae is None else str(ae)), "bulb_mode": self.is_bulb_mode(),
                "iso": prop(P_ISO, fmt_iso), "aperture": prop(P_APERTURE, fmt_aperture),
                "shutter": prop(P_SHUTTER, fmt_shutter),
                "liveview": {"on": self.liveview, "zoom": self.zoom, "center": self.zoom_center,
                             "fps": round(fps, 1), "seq": self.frame_seq,
                             "can_zoom": ZOOM in self.supported, "can_move": ZOOM_POSITION in self.supported,
                             "can_focus": DRIVE_LENS in self.supported},
                "bulb": dict(self.bulb, active=True, now=time.time()) if self.bulb else {"active": False},
                "autodownload": self.autodownload, "downloading": dl, "photos": list(self.photos),
                "out": OUT, "log": list(self.logs)[-40:],
            }


# ---------------------------------------------------------------- Webserver
class Handler(BaseHTTPRequestHandler):
    studio = None
    ACTIONS = {"/api/set": "act_set", "/api/liveview": "act_liveview", "/api/zoom": "act_zoom",
               "/api/zoommove": "act_zoommove", "/api/focus": "act_focus", "/api/bulb/start": "act_bulb_start",
               "/api/bulb/stop": "act_bulb_stop", "/api/shoot": "act_shoot",
               "/api/autodownload": "act_autodownload"}

    def log_message(self, *a):
        pass

    def send_bytes(self, data, ctype, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj).encode(), "application/json; charset=utf-8", code)

    def do_GET(self):
        u = urlparse(self.path)
        st = self.studio
        if u.path in ("/", "/index.html"):
            with open(INDEX_HTML, "rb") as f:
                self.send_bytes(f.read(), "text/html; charset=utf-8")
        elif u.path == "/api/state":
            self.send_json(st.snapshot())
        elif u.path == "/api/frame.jpg":
            after = int(parse_qs(u.query).get("after", ["0"])[0])
            with st.frame_cond:
                if st.frame_seq <= after:
                    st.frame_cond.wait(2.0)
                frame, seq = st.frame, st.frame_seq
            if frame and seq > after:
                self.send_bytes(frame, "image/jpeg", extra={"X-Frame-Seq": str(seq)})
            else:
                self.send_response(204)
                self.end_headers()
        elif u.path.startswith("/photos/") or u.path.startswith("/preview/"):
            base = PREVIEW_DIR if u.path.startswith("/preview/") else OUT
            name = os.path.basename(unquote(u.path.split("/", 2)[2]))
            path = os.path.join(base, name)
            if name and os.path.isfile(path):
                ctype = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "application/octet-stream"
                with open(path, "rb") as f:
                    self.send_bytes(f.read(), ctype)
            else:
                self.send_json({"error": "nicht gefunden"}, 404)
        else:
            self.send_json({"error": "nicht gefunden"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        method = self.ACTIONS.get(u.path)
        if not method:
            self.send_json({"ok": False, "error": "unbekannte Aktion"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            getattr(self.studio, method)(body)
            self.send_json({"ok": True})
        except CameraError as e:
            self.send_json({"ok": False, "error": str(e)})
        except Exception as e:
            self.studio.log("❌ Fehler bei %s: %r" % (u.path, e))
            self.send_json({"ok": False, "error": "Interner Fehler: %r" % e})


def main():
    guid = load_guid()
    name = socket.gethostname().split(".")[0]
    studio = Studio()
    Handler.studio = studio
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), Handler)
    except OSError as e:
        sys.exit("Port %d belegt – läuft 6D Studio schon? (%s)" % (WEB_PORT, e))
    httpd.daemon_threads = True
    threading.Thread(target=studio.camera_thread, args=(guid, name), daemon=True).start()
    threading.Thread(target=studio.download_worker, daemon=True).start()
    studio.log("6D Studio läuft – Bedienoberfläche: http://127.0.0.1:%d  (Fotos: %s)" % (WEB_PORT, OUT))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
