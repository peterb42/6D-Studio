#!/usr/bin/env python3
"""6D Studio – Fernsteuerung der Canon EOS 6D per WLAN oder USB-Kabel mit Bedienoberfläche im Browser.

Ein Prozess hält die Verbindung zur Kamera – PTP/IP über WLAN oder PTP über USB (ptpusb.py: eine Brücke, die für
das Studio aussieht wie die PTP/IP-Verbindung) – und bietet:
  * Bedienoberfläche   http://127.0.0.1:8066
      Livebild mit 5×/10×-Lupe der Kamera, Fokusantrieb, Schärfe-Anzeige für Sterne,
      Bulb Start/Stopp mit Timer, ISO/Blende/Zeit, automatischer Download,
      Serien für Stacking, Live-Stack und Kalibrier-Bibliothek (die letzten beiden brauchen numpy und
      rawpy aus der venv – siehe requirements.txt), Bildverwaltung mit Weitergabe an PixInsight und Siril.
  * Zugang für gphoto2 über ptpip:127.0.0.1 (wird vom Skript 6d-fernsteuerung genutzt).

Eigenheiten der 6D, die hier abgefangen werden:
  * Die PTP/IP-Anmeldung dauert ~4 s (libgphoto2 wartet nur 2,5 s).
  * Endet eine Session oder TCP-Verbindung, beendet die 6D die Fernsteuerung.
  * Ohne regelmäßige Abfragen trennt die 6D nach ~30 s.

Aufruf: 6d-studio [kamera-ip | usb]   – ohne Angabe: Verbindungsart aus ~/.gphoto/6d-verbindung (usb oder wlan)
Umgebung: CAM_PORT (15740), GPHOTO_PORT (15740), STUDIO_PORT (8066), OUT (~/Pictures/6D-Tethering),
          CAM_MODE (usb | wlan, statt der Datei), GPHOTO_DIR (~/.gphoto)
"""
import collections
import json
import math
import os
import queue
import re
import select
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

GPDIR = os.path.expanduser(os.environ.get("GPHOTO_DIR", "~/.gphoto"))
SETTINGS = os.path.join(GPDIR, "settings")
# Kamera-IP und Verbindungsart („usb“ oder „wlan“) – auch für 6d-fernsteuerung; das Simulator-Studio hat eigene
IPFILE = os.environ.get("CAM_IP_FILE") or os.path.join(GPDIR, "6d-wlan-ip")
MODE_FILE = os.environ.get("CAM_MODE_FILE") or os.path.join(GPDIR, "6d-verbindung")


def _default_ip():
    try:
        with open(IPFILE) as f:
            return f.read().strip() or "172.20.10.3"
    except OSError:
        return "172.20.10.3"


def _saved_mode():
    """Gemerkte Verbindungsart; ohne Datei wie früher: WLAN, wenn eine Kamera-IP gemerkt ist, sonst USB."""
    try:
        with open(MODE_FILE) as f:
            mode = f.read().strip().lower()
        if mode in ("usb", "wlan"):
            return mode
    except OSError:
        pass
    return "wlan" if os.path.isfile(IPFILE) and os.path.getsize(IPFILE) else "usb"


_ARG = sys.argv[1] if len(sys.argv) > 1 else ""
CAM_MODE = os.environ.get("CAM_MODE") or ("usb" if _ARG == "usb" else "wlan" if _ARG else _saved_mode())
CAM_IP = _ARG if _ARG and _ARG != "usb" else _default_ip()
# Ports – per Umgebungsvariable änderbar, damit ein Studio gegen den Simulator (sim/sim6d.py)
# parallel zum echten laufen kann.
CAM_PORT = int(os.environ.get("CAM_PORT", "15740"))         # PTP/IP-Port der Kamera
GPHOTO_PORT = int(os.environ.get("GPHOTO_PORT", "15740"))   # lokaler Zugang für gphoto2 (127.0.0.1)
WEB_PORT = int(os.environ.get("STUDIO_PORT", "8066"))
# MLDenoise-Modelle (.xmlm) für „Fertig entwickeln“ mit PixInsight auf diesem Mac (auf dem Rechner: <daten>/modelle)
MODEL_DIRS = [os.path.expanduser(os.environ.get("MODEL_DIR", "~/.config/6d-studio/modelle"))]
RECHNER_LIVE_EVERY = 20.0                                     # s: neue Rohbilder zum Rechner (wenn eingeschaltet)
OUT = os.path.expanduser(os.environ.get("OUT", "~/Pictures/6D-Tethering"))
DEFAULT_OUT = os.path.expanduser("~/Pictures/6D-Tethering")   # Kamera-Ablage (Simulator: …/Simulator)
PREVIEW_DIR = os.path.join(OUT, ".vorschau")
HERE = os.path.dirname(os.path.realpath(__file__))
INDEX_HTML = os.path.join(HERE, "6d-studio.html")
ASTRO_DIR = os.path.join(OUT, "Astro")              # Serien für Stacking (siehe astro.py)

sys.path.insert(0, HERE)                            # -I lässt den Skriptordner sonst weg
import astro  # noqa: E402
import calib  # noqa: E402
import gallery  # noqa: E402
import imaging  # noqa: E402
import livestack  # noqa: E402
import ptpusb  # noqa: E402
import rechner  # noqa: E402

LIBRARY_DIR = os.path.join(ASTRO_DIR, calib.LIBRARY_FOLDER)
DOWNLOAD_MODES = ("all", "raw", "jpeg", "none")    # was eine Serie auf den Mac lädt

# ---------------------------------------------------------------- Protokoll-Konstanten
INIT_CMD_REQ, INIT_CMD_ACK, INIT_EVT_REQ, INIT_EVT_ACK, INIT_FAIL = 1, 2, 3, 4, 5
OP_REQ, OP_RESP, EVENT, START_DATA, DATA, CANCEL, END_DATA = 6, 7, 8, 9, 10, 11, 12
PROBE_REQ, PROBE_RESP = 13, 14
PROBE_RESP_PKT = struct.pack("<II", 8, PROBE_RESP)

GET_DEVICE_INFO, OPEN_SESSION, CLOSE_SESSION = 0x1001, 0x1002, 0x1003
GET_OBJECT_INFO, GET_OBJECT, GET_PARTIAL_OBJECT = 0x1008, 0x1009, 0x101B
DOWNLOAD_CHUNK = 256 * 1024          # Download in Stücken: Bulb-Befehle kommen dazwischen dran (Kabel: ~10 MB/s,
                                     # mit 2-MB-Stücken nicht schneller – das Tempo bestimmt die 6D)
SERIES_FILE_GRACE = 0.8              # nach dem ersten Bild kurz warten (RAW+JPEG = zwei Dateien)
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


def shutter_seconds(v):
    """Nominelle Belichtungszeit in Sekunden (wie auf der Kamera angezeigt), None bei Bulb/Auto."""
    if v is None or v in (0, SHUTTER_BULB) or v > 0xA0:
        return None
    return _nearest(T_NOMINAL, 2 ** (-(v - 0x38) / 8))


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
        self.ops = collections.deque()      # Befehle aus anderen Threads; vorne = Vorrang
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
        # Serie für Stacking
        self.series = None                  # Zustand der laufenden/letzten Serie
        self.pending_frame = None           # Bild der Serie, dem neue Kameradateien zugeordnet werden
        # Live-Stack und Kalibrier-Bibliothek
        self.library = calib.Library(LIBRARY_DIR)
        self.live = None                    # livestack.LiveStack
        self.master_job = None              # Master-Erstellung (läuft im Hintergrund)
        self.last_light = None              # ISO, Zeit, Temperatur der letzten Lights (für passende Darks)
        self.used_index = {}                # (Ordner, Typ) → höchste vergebene Bildnummer (Downloads hinken nach)
        self.last_flat = None               # ISO, Zeit (Median), Blende, Helligkeit der letzten Flats (für Dark-Flats)
        self.gallery_job = None             # Auftrag der Nachbearbeitung (Prüfen, Siril)
        self._libraries = {}                # Bibliotheken weiterer Ablagen (Bildverwaltung)
        # Rechner (rechner/6d-rechner.py): Einstellungen, letzter Zustand, Vorab-Hochladen während der Serie
        self.rechner_cfg = rechner.load_config()
        self._rechner_cache = {"t": 0.0, "url": None, "status": None, "error": None}
        self._rechner_known = {}            # Sitzung auf dem Rechner → {Pfad: {size}} (was schon oben ist)
        self._rechner_sync_lock = threading.Lock()
        self.rechner_live = None            # {session, uploaded, total, time, error}
        # Verbindung: WLAN (PTP/IP) oder USB-Kabel (ptpusb.Bridge – sieht für den Rest aus wie PTP/IP)
        self.conn_mode, self.cam_ip = CAM_MODE, CAM_IP
        self.bridge = self._old_bridge = None
        self.usb_open = ptpusb.open_camera  # Tests: Ersatzgerät
        self._login_socks = []              # Sockets der laufenden WLAN-Anmeldung (Umschalten bricht sie ab)
        self.cam_cmd = self.cam_evt = None
        self.cli_cmd = self.cli_evt = None
        self.cmd_ack = self.evt_ack = None
        self.tid = 0
        self.inflight = None
        self.client_queue = []
        self.internal_queue = []
        self.last_event = self.last_frame_req = self.last_cam = 0.0
        self.gphoto_port = None
        os.makedirs(PREVIEW_DIR, exist_ok=True)

    # ---------- Protokoll
    def log(self, *a):
        line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
        print(line, flush=True)
        with self.lock:
            self.logs.append(line)

    # ---------- Anmeldung
    def _mode_check(self, mode):
        if self.conn_mode != mode:
            raise CameraLost("Verbindungsart gewechselt")

    def login(self, guid, name):
        """WLAN: PTP/IP-Anmeldung (Befehls- und Ereigniskanal), dann die Session."""
        if guid is None:
            raise CameraLost("Keine WLAN-Kopplung (ptp2_ip-GUID in ~/.gphoto/settings) – zuerst koppeln, "
                             "siehe README „Erstes Koppeln“")
        ip = self.cam_ip
        with self.lock:
            self.status = "Warte auf die Kamera"
        self.log("Warte auf die Kamera", ip, "…")
        while not ping(ip):
            self._mode_check("wlan")
            time.sleep(1)
        while True:
            self._mode_check("wlan")
            try:
                cmd = socket.create_connection((ip, CAM_PORT), timeout=5)
                break
            except OSError:
                time.sleep(1)
        self._login_socks = [cmd]
        with self.lock:
            self.status = "Melde mich an"
        self.log("Melde mich bei der Kamera an (bis 90 s, ggf. an der Kamera OK drücken) …")
        body = guid + (name + "\0").encode("utf-16-le") + struct.pack("<HH", 0, 1)
        cmd.sendall(struct.pack("<II", 8 + len(body), INIT_CMD_REQ) + body)
        ack = recv_packet(cmd, 90)
        self._mode_check("wlan")
        if ptype(ack) == INIT_FAIL:
            raise CameraLost("Kamera lehnt die Anmeldung ab – an der Kamera die Kopplung neu starten")
        if ptype(ack) != INIT_CMD_ACK:
            raise CameraLost("unerwartete Antwort Typ %d" % ptype(ack))
        conn_id = struct.unpack_from("<I", ack, 8)[0]
        evt = socket.create_connection((ip, CAM_PORT), timeout=10)
        self._login_socks.append(evt)
        evt.sendall(struct.pack("<III", 12, INIT_EVT_REQ, conn_id))
        evt_ack = recv_packet(evt, 30)
        if ptype(evt_ack) != INIT_EVT_ACK:
            raise CameraLost("Event-Kanal abgelehnt")
        for s in (cmd, evt):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self._start_session(cmd, evt, ack, evt_ack, "WLAN %s" % ip)

    def login_usb(self):
        """USB-Kabel: Kamera öffnen, Brücke starten, dann dieselbe Session wie über WLAN."""
        with self.lock:
            self.status = "Warte auf die Kamera am Kabel"
        self.log("Warte auf die Kamera am USB-Kabel (Wi-Fi an der Kamera aus) …")
        if self._old_bridge:                             # hängt die alte Brücke noch in einem Transfer: abwarten
            self._old_bridge.done.wait(ptpusb.READ_TIMEOUT + 5)
            self._old_bridge = None
        said = None
        while True:
            self._mode_check("usb")
            try:
                dev = self.usb_open(log=self.log)
                break
            except ptpusb.UsbError as e:
                if str(e) != said:                       # jede neue Meldung einmal, nicht alle 2 s
                    said = str(e)
                    self.log("⏳ %s" % e)
                    with self.lock:
                        self.status = "Warte auf die Kamera am Kabel – %s" % e
                time.sleep(2)
        self.log("USB: Kamera gefunden (%s)" % dev.where)
        self.bridge = ptpusb.Bridge(ptpusb.PtpUsb(dev, log=self.log), log=self.log)
        cmd_ack, evt_ack = ptpusb.init_acks()
        self._start_session(self.bridge.cmd, self.bridge.evt, cmd_ack, evt_ack, "USB")

    def _start_session(self, cmd, evt, cmd_ack, evt_ack, how):
        """OpenSession, GetDeviceInfo, dann der Kamera-Thread mit cmd/evt – gleich für WLAN und USB."""
        rc, _ = sync_op(cmd, 0, OPEN_SESSION, 1)
        if rc not in (RC_OK, RC_SESSION_OPEN):
            raise CameraLost("OpenSession abgelehnt: " + rc_text(rc))
        rc, info = sync_op(cmd, 1, GET_DEVICE_INFO)
        supported, model = parse_device_info(info) if rc == RC_OK else (set(), "")
        for s in (cmd, evt):
            s.settimeout(None)
        with self.lock:
            self._login_socks = []
            self.cam_cmd, self.cam_evt = cmd, evt
            self.r_cam_cmd, self.r_cam_evt = Reader(cmd), Reader(evt)
            self.cmd_ack, self.evt_ack = cmd_ack, evt_ack
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
        self.log("✅ Kamera verbunden (%s, %s), Session offen – Oberfläche: http://127.0.0.1:%d" % (
            self.model, how, WEB_PORT))

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
            if self.bridge:                          # USB: die Brücke gibt das Kabel in ihrem Faden frei
                self.bridge.close()
                self._old_bridge, self.bridge = self.bridge, None
            for s in self._login_socks + [self.cam_cmd, self.cam_evt, self.cli_cmd, self.cli_evt]:
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass
            self.cam_cmd = self.cam_evt = self.cli_cmd = self.cli_evt = None
            self._login_socks = []
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
        while self.ops:
            pending.append(self.ops.popleft())
        for op in pending:
            op.done.set()

    # ---------- Befehle aus anderen Threads
    def submit(self, code, params=(), data=None, timeout=15, name=None, priority=False):
        """Befehl an die Kamera und auf die Antwort warten. priority: vor wartende Befehle stellen
        (Bulb Start/Stopp sollen nicht hinter einem Download-Stück warten)."""
        if not self.connected:
            raise CameraError("Kamera nicht verbunden")
        op = Op(code, params, data, timeout, name=name)
        if priority:
            self.ops.appendleft(op)
        else:
            self.ops.append(op)
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
        if self.ops:
            self.send_op(self.ops.popleft())
            return
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
        lst.bind(("127.0.0.1", GPHOTO_PORT))
        lst.listen(4)
        self.gphoto_port = lst.getsockname()[1]
        while True:
            mode = self.conn_mode
            try:
                if mode == "usb":
                    self.login_usb()
                else:
                    self.login(guid, name)
                self.run(lst)
            except (CameraLost, ConnectionError, OSError, ValueError, ptpusb.UsbError) as e:
                if self.conn_mode == mode:
                    self.log("❌", e)
            except Exception as e:
                self.log("❌ Unerwarteter Fehler:", repr(e))
            self.disconnect()
            if self.conn_mode != mode:                   # umgeschaltet: gleich mit der neuen Verbindungsart
                continue
            with self.lock:
                self.status = ("Getrennt – Kabel und Kamera prüfen" if mode == "usb" else
                               "Getrennt – an der Kamera „Fernsteuerung (EOS Utility)“ neu starten")
            self.log("Warte auf erneute Verbindung der Kamera …")
            for _ in range(15):
                if self.conn_mode != mode:
                    break
                time.sleep(0.2)

    # ---------- Verbindungsart: WLAN oder Kabel
    def connection_info(self):
        return {"mode": self.conn_mode, "ip": self.cam_ip, "usb": ptpusb.missing()}

    def act_connection(self, body):
        """Zwischen WLAN und USB-Kabel umschalten (gemerkt in ~/.gphoto/6d-verbindung, auch für 6d-fernsteuerung).
        Die bestehende Verbindung wird getrennt, der Kamera-Thread verbindet gleich neu."""
        mode = body.get("mode")
        if mode not in ("usb", "wlan"):
            raise CameraError("Verbindungsart: usb oder wlan")
        ip = (body.get("ip") or "").strip()
        if ip and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            raise CameraError("IP-Adresse der Kamera wie 172.20.10.3")
        if (self.bulb or self.series_active()) and mode != self.conn_mode:
            raise CameraError("Erst die Belichtung bzw. Serie beenden – Umschalten trennt die Kamera")
        if mode == "usb" and ptpusb.missing():
            raise CameraError(ptpusb.missing())
        os.makedirs(GPDIR, exist_ok=True)
        if mode == "wlan" and ip:
            with open(IPFILE, "w") as f:
                f.write(ip + "\n")
        with open(MODE_FILE, "w") as f:
            f.write(mode + "\n")
        changed = mode != self.conn_mode or (mode == "wlan" and ip and ip != self.cam_ip)
        with self.lock:
            if ip:
                self.cam_ip = ip
            self.conn_mode = mode
            if changed:
                self.status = "Wechsle auf %s" % ("das Kabel" if mode == "usb" else "WLAN")
                for s in self._login_socks + [self.cam_cmd]:   # bricht Anmeldung bzw. Verbindung ab
                    if s:
                        try:
                            s.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
        if changed:
            self.log("Verbindung: %s" % ("USB-Kabel – an der Kamera Wi-Fi aus, Kabel einstecken" if mode == "usb"
                                         else "WLAN %s – an der Kamera Wi-Fi an, „Fernsteuerung (EOS Utility)“"
                                         % self.cam_ip))
        return {"connection": self.connection_info()}

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
                        data = struct.pack("<III", 12, P_CAPTURE_DEST, card[0])
                        self.internal_queue.append(Op(SET_PROP, data=data, name="Speicherort Karte"))
                        self.log("Speicherort auf Speicherkarte gestellt.")
        for handle, transfer in new_objects:
            frame = self.pending_frame                   # gehört das Bild zu einer laufenden Serie?
            if frame is not None:
                frame["handles"].append(handle)
            if frame is not None or transfer or self.autodownload:
                self.downloads.put((handle, transfer, frame))

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

    def series_active(self):
        return bool(self.series and self.series["active"])

    def act_bulb_start(self, body):
        if self.series_active():
            raise CameraError("Es läuft eine Serie – dort stoppen")
        self.bulb_start(max(0, int(float(body.get("seconds") or 0))))

    def bulb_start(self, seconds=0, quiet=False):
        with self.lock:
            if self.bulb:
                raise CameraError("Es läuft bereits eine Belichtung")
        if self.props and not self.is_bulb_mode():
            raise CameraError("Für Bulb das Moduswahlrad der Kamera auf B stellen")
        if BULB_START in self.supported:
            method, op = "bulb", self.submit(BULB_START, name="Bulb Start", timeout=20, priority=True)
        elif RELEASE_ON in self.supported:
            method, op = "release", self.submit(RELEASE_ON, (3, 0), name="Bulb Start", timeout=20, priority=True)
        else:
            raise CameraError("Kamera unterstützt keinen Bulb-Start über WLAN")
        rc = op.effective_rc()
        if rc != RC_OK and method == "bulb" and RELEASE_ON in self.supported:
            method, op = "release", self.submit(RELEASE_ON, (3, 0), name="Bulb Start", timeout=20, priority=True)
            rc = op.effective_rc()
        if rc != RC_OK:
            raise CameraError("Belichtung startet nicht: %s" % rc_text(rc))
        with self.lock:
            self.bulb = {"start": time.time(), "planned": seconds, "method": method}
            if seconds:
                self.bulb_timer = threading.Timer(seconds, self._bulb_autostop)
                self.bulb_timer.daemon = True
                self.bulb_timer.start()
        if not quiet:
            self.log("● Bulb-Belichtung gestartet"
                     + (" – Auto-Stopp nach %s" % fmt_duration(seconds) if seconds else ""))

    def _bulb_autostop(self):
        try:
            self.bulb_stop()
        except CameraError as e:
            self.log("❌ Auto-Stopp:", e)

    def act_bulb_stop(self, body):
        if self.series_active():
            raise CameraError("Es läuft eine Serie – dort stoppen")
        self.bulb_stop()

    def bulb_stop(self, quiet=False):
        """Beendet die Bulb-Belichtung und gibt die tatsächliche Belichtungszeit zurück."""
        with self.lock:
            b = self.bulb
            if not b:
                raise CameraError("Es läuft keine Belichtung")
            if self.bulb_timer:
                self.bulb_timer.cancel()
                self.bulb_timer = None
        if b["method"] == "bulb":
            op = self.submit(BULB_END, name="Bulb Stopp", timeout=30, priority=True)
        else:
            op = self.submit(RELEASE_OFF, (3,), name="Bulb Stopp", timeout=30, priority=True)
        rc = op.effective_rc()
        if rc != RC_OK:
            raise CameraError("Stopp fehlgeschlagen: %s – bitte erneut drücken" % rc_text(rc))
        duration = time.time() - b["start"]
        with self.lock:
            self.bulb = None
        if not quiet:
            self.log("■ Belichtung beendet nach %s" % fmt_duration(duration))
        return duration

    def act_shoot(self, body):
        if self.series_active():
            raise CameraError("Es läuft eine Serie – dort stoppen")
        self.shoot()

    def shoot(self, quiet=False):
        if self.is_bulb_mode():
            raise CameraError("Im Modus B bitte Bulb Start/Stopp verwenden")
        if REMOTE_RELEASE in self.supported:
            op = self.submit(REMOTE_RELEASE, name="Auslösen", timeout=60, priority=True)
            rc = op.effective_rc()
            if rc == RC_OK and op.rparams and op.rparams[0] not in (0,):
                raise CameraError("Kamera hat nicht ausgelöst (Code %d) – bei Nacht Objektiv auf MF stellen"
                                  % op.rparams[0])
        else:
            op = self.submit(RELEASE_ON, (3, 0), name="Auslösen", timeout=60, priority=True)
            rc = op.effective_rc()
            self.submit(RELEASE_OFF, (3,), name="Auslösen loslassen", timeout=60, priority=True)
        if rc != RC_OK:
            raise CameraError("Auslösen fehlgeschlagen: %s" % rc_text(rc))
        if not quiet:
            self.log("📷 Ausgelöst")

    # ---------- Serie für Stacking
    def act_series_start(self, body):
        """Startet eine Serie gleichartiger Bilder (Light/Dark/Bias/Flat/Dark-Flat) – siehe astro.py."""
        if not self.connected:
            raise CameraError("Kamera nicht verbunden")
        with self.lock:
            if self.series_active():
                raise CameraError("Es läuft bereits eine Serie")
            if self.bulb:
                raise CameraError("Es läuft gerade eine Belichtung")
        ftype = body.get("type", "light")
        if ftype not in astro.FRAME_TYPES:
            raise CameraError("Unbekannter Bildtyp")
        label = astro.FRAME_TYPES[ftype]["label"]
        count = int(body.get("count") or 1)
        if not 1 <= count <= 999:
            raise CameraError("Anzahl 1 bis 999")
        pause = clamp(float(body.get("pause") or 0), 0, 600)
        bulb = self.is_bulb_mode()
        exposure = float(body.get("exposure") or 0) if bulb else None
        if bulb and ftype in ("bias", "flat", "darkflat"):
            raise CameraError("%s: Moduswahlrad auf %s stellen" % (label, "Av" if ftype == "flat" else "M"))
        if ftype in ("bias", "darkflat") and self.props.get(P_AEMODE) not in (None, 3):
            raise CameraError("%s: Moduswahlrad auf M stellen – das Studio stellt die Zeit ein" % label)
        if bulb and exposure <= 0:
            raise CameraError("Belichtungszeit für Bulb angeben")
        download = body.get("download", "all" if ftype == "light" else "none")
        download = {True: "all", False: "none", None: "none"}.get(download, download)
        if download not in DOWNLOAD_MODES:
            raise CameraError("Unbekannte Download-Einstellung")
        master = bool(body.get("master")) and ftype in calib.KINDS     # danach Master für die Bibliothek
        if master:
            problem = imaging.missing()
            if problem:
                raise CameraError("Master erstellen geht nicht: " + problem)
            if count < calib.MIN_FRAMES:
                raise CameraError("Für einen Master mindestens %d Bilder" % calib.MIN_FRAMES)
            if self.master_job and self.master_job["active"]:
                raise CameraError("Es wird gerade ein Master erstellt – bitte warten")
            download = "raw"
        if master and ftype == "dark":                       # z. B. Kamera steht noch auf 1/4000 von den Bias
            exp = exposure if bulb else shutter_seconds(self.props.get(P_SHUTTER))
            if not exp:
                raise CameraError("Darks: Moduswahlrad auf B oder M und die Zeit der Lights einstellen")
            if exp < calib.DARK_MIN_EXPOSURE:
                raise CameraError("Darks mit %s wären nur Bias – an der Kamera die Zeit der Lights einstellen (ab 1 s)"
                                  % ("%g s" % exp if bulb else fmt_shutter(self.props.get(P_SHUTTER))))
        if ftype == "bias":                                          # kürzeste Zeit einstellen
            fastest = max(self.avail.get(P_SHUTTER) or [0])
            if fastest:
                if self.props.get(P_SHUTTER) != fastest:
                    self.log("Bias: Belichtungszeit → %s" % fmt_shutter(fastest))
                self._set_and_wait(P_SHUTTER, fastest, "Belichtungszeit")     # immer: die Anzeige kann hinterherhinken
        if ftype == "darkflat" and self.last_flat:                   # ISO und Zeit der Flats einstellen
            self._match_flats()
        now = time.time()
        target = astro.safe_name(body.get("target"))
        session = astro.session_dir(ASTRO_DIR, target, now)
        folder = os.path.join(session, astro.FRAME_TYPES[ftype]["folder"])
        os.makedirs(folder, exist_ok=True)
        with self.lock:
            self.series = {
                "active": True, "type": ftype, "label": label, "target": target, "count": count,
                "exposure": exposure, "pause": pause, "bulb": bulb, "download": download, "master": master,
                "session": session, "folder": folder,
                "first": max(astro.next_index(folder, ftype), self.used_index.get((folder, ftype), 0) + 1),
                "done": 0, "index": 0, "phase": "startet", "phase_end": None, "started": now,
                "stop": 0, "error": None, "last": None, "files": [],
            }
        self.log("▶︎ Serie: %d × %s (%s) → %s%s" % (
            count, label, fmt_duration(exposure) if bulb else "Kamerazeit " + fmt_shutter(self.props.get(P_SHUTTER)),
            folder.replace(os.path.expanduser("~"), "~"), " – danach Master für die Bibliothek" if master else ""))
        threading.Thread(target=self.series_thread, daemon=True).start()

    def _set_and_wait(self, code, value, name):
        self.set_prop(code, value, name=name)
        end = time.time() + 5                      # bis die Kamera den neuen Wert meldet (für den Dateinamen)
        while self.props.get(code) != value and time.time() < end:
            time.sleep(0.05)

    def _match_flats(self):
        """Dark-Flats brauchen ISO und Belichtungszeit der Flats – das Studio stellt beides ein. Immer senden, auch
        wenn die Werte schon zu stimmen scheinen: eine Änderung an der Kamera kann noch unterwegs sein."""
        lf = self.last_flat
        parts = []
        iso = next((c for c in self.avail.get(P_ISO) or [] if fmt_iso(c) == str(lf.get("iso"))), None)
        if iso:
            self._set_and_wait(P_ISO, iso, "ISO")
            parts.append("ISO " + fmt_iso(iso))
        codes = [c for c in self.avail.get(P_SHUTTER) or [] if shutter_seconds(c)]
        if lf.get("exposure") and codes:
            best = min(codes, key=lambda c: abs(math.log(shutter_seconds(c) / lf["exposure"])))
            self._set_and_wait(P_SHUTTER, best, "Belichtungszeit")
            parts.append(fmt_shutter(best))
        if parts:
            self.log("Dark-Flats wie die Flats: %s" % ", ".join(parts))

    def act_series_stop(self, body):
        s = self.series
        if not s or not s["active"]:
            raise CameraError("Es läuft keine Serie")
        s["stop"] = 2 if body.get("now") else max(s["stop"], 1)
        self.log("Serie wird %s beendet" % ("sofort" if s["stop"] == 2 else "nach diesem Bild"))

    def _series_wait(self, s, phase, seconds, stop_level):
        """Warten mit Anzeige; endet früher, wenn die Serie mit mindestens stop_level gestoppt wird."""
        s["phase"], s["phase_end"] = phase, time.time() + seconds
        while time.time() < s["phase_end"] and s["stop"] < stop_level and self.connected:
            time.sleep(0.05)
        s["phase_end"] = None

    def series_thread(self):
        s = self.series
        try:
            for n in range(s["count"]):
                if s["stop"]:
                    break
                self.series_frame(s, s["first"] + n)
                s["done"] += 1
                if n < s["count"] - 1 and s["pause"] and not s["stop"]:
                    self._series_wait(s, "Pause", s["pause"], stop_level=1)
        except CameraError as e:
            s["error"] = str(e)
            self.log("❌ Serie abgebrochen:", e)
        finally:
            with self.lock:
                self.pending_frame = None
                s["active"], s["phase"], s["phase_end"] = False, "fertig", None
            self.log("■ Serie %s: %d von %d Bildern" % (s["label"], s["done"], s["count"]))
            if s["master"]:
                if s["done"] >= calib.MIN_FRAMES:
                    threading.Thread(target=self.master_from_series, args=(s,), daemon=True).start()
                else:
                    self.log("Kein Master: nur %d Bilder (mindestens %d)" % (s["done"], calib.MIN_FRAMES))

    def series_frame(self, s, index):
        start = time.time()
        with self.lock:
            p = dict(self.props)
        shutter = p.get(P_SHUTTER)
        planned = s["exposure"] if s["bulb"] else shutter_seconds(shutter)
        frame = {
            "base": astro.frame_name(s["type"], s["target"], start, fmt_iso(p.get(P_ISO)), planned,
                                     fmt_aperture(p.get(P_APERTURE)), index),
            "type": s["type"], "index": index, "session": s["session"], "folder": s["folder"], "sid": s["started"],
            "download": s["download"], "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)),
            "planned": planned, "exposure": None, "iso": fmt_iso(p.get(P_ISO)),
            "aperture": fmt_aperture(p.get(P_APERTURE)), "shutter": fmt_shutter(shutter),
            "mode": AE_MODES.get(p.get(P_AEMODE), "?"), "handles": [], "files": s["files"],
        }
        with self.lock:
            self.pending_frame = frame
            s["index"], s["last"] = index - s["first"] + 1, frame["base"]
            key = (s["folder"], s["type"])
            self.used_index[key] = max(self.used_index.get(key, 0), index)
        if s["bulb"]:
            self.bulb_start(quiet=True)
            self._series_wait(s, "belichtet", planned, stop_level=2)
            frame["exposure"] = round(self.bulb_stop(quiet=True), 2)
        else:
            self.shoot(quiet=True)
            frame["exposure"] = planned or None
            if planned:
                self._series_wait(s, "belichtet", planned, stop_level=2)
        # Warten, bis die Kamera das Bild meldet – erst dann kommt das nächste (Zuordnung der Dateien).
        s["phase"] = "speichert"
        deadline = time.time() + 30 + (planned or 0)
        while not frame["handles"] and time.time() < deadline and self.connected:
            time.sleep(0.05)
        if frame["handles"]:
            time.sleep(SERIES_FILE_GRACE)
            self.log("● %s %d/%d: %s" % (s["label"], s["index"], s["count"], frame["base"]))
        else:
            self.log("⚠️ Kamera hat kein Bild für %s gemeldet" % frame["base"])
        with self.lock:
            self.pending_frame = None

    def act_autodownload(self, body):
        self.autodownload = bool(body.get("on"))
        self.log("Automatischer Download %s" % ("an" if self.autodownload else "aus"))

    # ---------- Live-Stack (livestack.py)
    def live_running(self):
        return bool(self.live and self.live.active)

    def _live_or_error(self):
        if not self.live:
            raise CameraError("Kein Live-Stack – zuerst starten")
        return self.live

    def act_live_start(self, body, session=None):
        problem = imaging.missing()
        if problem:
            raise CameraError("Live-Stack geht nicht: " + problem)
        if self.live_running():
            raise CameraError("Der Live-Stack läuft schon")
        session = session or astro.session_dir(ASTRO_DIR, body.get("target"), time.time())
        opts = {k: body.get(k) for k in livestack.LiveStack.OPTIONS}
        self.live = livestack.LiveStack(self.library, os.path.join(session, "livestack"), opts, self.log)
        self.log("▶︎ Live-Stack an – jedes neue Light wird eingerechnet (Kalibrierung: Bias %s, Dark %s, Flat %s)" % (
            opts.get("bias") or "auto", opts.get("dark") or "auto", opts.get("flat") or "auto"))

    def act_live_stop(self, body):
        self._live_or_error().finish()
        self.log("■ Live-Stack: keine neuen Bilder mehr – wird gespeichert")

    def act_live_reset(self, body):
        self._live_or_error().reset()

    def act_live_view(self, body):
        self._live_or_error().set_view(body.get("stretch"), body.get("black"), body.get("flatten"))

    def act_live_analyze(self, body):
        live = self._live_or_error()
        if not live.frames:
            raise CameraError("Noch nichts gestapelt")
        live.request_analysis()

    def act_live_save(self, body):
        try:
            self._live_or_error().save()
        except RuntimeError as e:
            raise CameraError(str(e)) from None

    def act_live_folder(self, body):
        """Vorhandene Lights einer Sitzung (nach)stacken – zum Ausprobieren auch ohne Kamera."""
        session = body.get("session") or ""
        try:
            files = calib.source_files(ASTRO_DIR, session, "lights")
        except ValueError as e:
            raise CameraError(str(e)) from None
        raws = [f for f in files if imaging.kind_of(f) == "raw"]
        files = raws or [f for f in files if imaging.kind_of(f) == "jpeg"]
        if not files:
            raise CameraError("Keine Lights in %s" % session)
        if not self.live_running():
            self.act_live_start(body, session=os.path.join(ASTRO_DIR, session))
        for f in files:
            self.live.add(f)
        self.log("Live-Stack: %d Lights aus %s eingereiht" % (len(files), session))

    # ---------- Kalibrier-Bibliothek (calib.py)
    # ---------- Bildverwaltung (gallery.py)
    def gallery_roots(self):
        """Ablagen: die des Studios zuerst, dazu die Kamera- bzw. Simulator-Ablage – so zeigt die Bildverwaltung
        z. B. die Live-Stacks der Kamera auch dann, wenn gerade das Simulator-Studio läuft."""
        sim = os.path.join(DEFAULT_OUT, "Simulator")

        def same(a, b):
            return os.path.realpath(a) == os.path.realpath(b)
        own = "Simulator" if same(OUT, sim) else "Kamera" if same(OUT, DEFAULT_OUT) else "Fotos"
        roots = [{"key": "studio", "label": own, "path": OUT}]
        for key, label, path in (("kamera", "Kamera", DEFAULT_OUT), ("simulator", "Simulator", sim)):
            if os.path.isdir(path) and not any(same(path, r["path"]) for r in roots):
                roots.append({"key": key, "label": label, "path": path})
        return roots

    def _root(self, key):
        for r in self.gallery_roots():
            if r["key"] == (key or "studio"):
                return r["path"]
        raise CameraError("Unbekannte Ablage")

    def _library_for(self, path):
        if os.path.realpath(path) == os.path.realpath(OUT):
            return self.library
        if path not in self._libraries:
            self._libraries[path] = calib.Library(os.path.join(path, "Astro", calib.LIBRARY_FOLDER))
        return self._libraries[path]

    def gallery_info(self):
        found = gallery.find_tools()
        home = os.path.expanduser("~")
        pi = gallery.pixinsight_cli(found)
        return {"roots": [dict(r, path=r["path"].replace(home, "~"), index=gallery.index(r["path"]))
                          for r in self.gallery_roots()],
                "tools": gallery.tools_info(found), "siril": bool(gallery.siril_cli(found)),
                "pixinsight": bool(pi), "wbpp": bool(pi and gallery.wbpp_script(pi)), "rechner": self.rechner_info(),
                "mldenoise": os.path.basename(gallery.find_model(MODEL_DIRS) or "") or None,
                "frame_types": {k: v["label"] for k, v in astro.FRAME_TYPES.items()}}

    def gallery_session(self, root, session):
        out = self._root(root)
        try:
            return gallery.session_summary(out, session, self._library_for(out))
        except ValueError as e:
            raise CameraError(str(e)) from None

    def act_gallery_open(self, body):
        try:
            gallery.open_with(self._root(body.get("root")), body.get("rels") or [], body.get("tool"),
                              bool(body.get("reveal")))
        except ValueError as e:
            raise CameraError(str(e)) from None

    def act_gallery_adopt(self, body):
        """Einzelaufnahmen als Lights (oder Kalibrierbilder) in eine Sitzung übernehmen."""
        out = self._root(body.get("root"))
        try:
            with self.lock:
                own = os.path.realpath(out) == os.path.realpath(OUT)
                new = gallery.adopt(out, body.get("rels") or [], body.get("type") or "light", body.get("target"),
                                    used=self.used_index if own else {})
        except (ValueError, OSError) as e:
            raise CameraError(str(e)) from None
        self.log("Übernommen: %d Datei(en) → %s" % (len(new), os.path.dirname(new[0]) if new else "–"))
        return {"rels": new}

    def act_gallery_cull(self, body):
        restore = bool(body.get("restore"))
        try:
            n = gallery.cull(self._root(body.get("root")), body.get("rels") or [], restore=restore)
        except (ValueError, OSError) as e:
            raise CameraError(str(e)) from None
        self.log("%s: %d Datei(en)" % ("Zurückgelegt" if restore else "Aussortiert", n))
        return {"moved": n}

    def act_gallery_export(self, body):
        """PixInsight (WBPP): verknüpfte Rohbilder je Typ, Listen und bilder.csv in <Sitzung>/pixinsight/."""
        out = self._root(body.get("root"))
        try:
            res = gallery.export_pixinsight(out, body.get("session"), self._library_for(out))
        except (ValueError, OSError) as e:
            raise CameraError(str(e)) from None
        self.log("PixInsight-Export: %s → %s" % (", ".join("%d %s" % (n, astro.FRAME_TYPES[t]["label"])
                                                            for t, n in res["counts"].items()),
                                                  res["folder"].replace(os.path.expanduser("~"), "~")))
        if body.get("reveal", True):
            gallery.open_with(out, [gallery.rel_of(out, os.path.join(res["folder"], "LIESMICH.txt"))], reveal=True)
        return {"export": res}

    def act_gallery_script(self, body):
        """Nur den Siril-Arbeitsordner mit Skript anlegen (zum Ausführen in Siril selbst)."""
        out = self._root(body.get("root"))
        try:
            res = gallery.prepare_siril(out, body.get("session"), self._library_for(out))
        except (ValueError, OSError) as e:
            raise CameraError(str(e)) from None
        if body.get("reveal", True):
            gallery.open_with(out, [gallery.rel_of(out, res["script"])], reveal=True)
        return {"siril": res}

    # Aufträge der Nachbearbeitung – einer zur Zeit, Fortschritt in gallery_job
    def _start_job(self, kind, label, root, session, work, tool=None):
        if self.gallery_job and self.gallery_job["active"]:
            raise CameraError("Es läuft schon: %s" % self.gallery_job["label"])
        job = {"kind": kind, "label": label, "root": root or "studio", "session": session, "active": True,
               "tool": tool, "steps": [], "step": "startet", "index": 0, "percent": 0.0, "detail": "",
               "error": None, "warnings": [], "result": None, "results": [], "freed": 0, "check": None,
               "started": time.time(), "finished": None}
        self.gallery_job = job

        def run():
            try:
                work(job)
                job["step"] = "fertig"
            except Exception as e:
                job["error"] = str(e)
                self.log("❌ %s: %s" % (label, e))
            finally:
                job["active"] = False
                job["finished"] = time.time()
        threading.Thread(target=run, daemon=True).start()
        return job

    def _siril(self, job, folder, script):
        """siril-cli -d <Ordner> -s <Skript>. Schritt aus den Kommentaren „# >> …“ (Siril gibt sie wörtlich aus,
        seine eigenen Meldungen sind übersetzt), Prozent aus „progress: …, 42.00%“."""
        cli = gallery.siril_cli()
        if not cli:
            raise RuntimeError("Siril ist nicht installiert (siril.org)")
        last = ""
        with subprocess.Popen([cli, "-d", folder, "-s", script], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace") as p:
            for line in p.stdout:
                line = line.strip().split("\r")[-1]
                if line.startswith("log: # >> "):
                    job["step"], job["percent"] = line[10:].strip(), 0.0
                    job["index"] += 1
                elif "progress:" in line:
                    m = re.search(r"([0-9]+(?:\.[0-9]+)?)%", line)
                    if m:
                        job["percent"] = min(100.0, float(m.group(1)))
                elif line.startswith("log: ") and not line.startswith("log: #"):
                    last = line[5:]
                    job["detail"] = last[:160]
            rc = p.wait()
        if rc != 0:
            raise RuntimeError("Siril meldet einen Fehler (Code %d): %s" % (rc, last))

    def _tool(self, body):
        tool = body.get("tool") or "siril"
        if tool == "siril":
            if not gallery.siril_cli():
                raise CameraError("Siril ist nicht installiert (siril.org)")
        elif tool == "pixinsight":
            if not gallery.pixinsight_cli():
                raise CameraError("PixInsight ist nicht installiert (pixinsight.com)")
        elif tool == "rechner":
            self._rechner_client()
        else:
            raise CameraError("Unbekanntes Programm: %s" % tool)
        return tool

    def act_gallery_check(self, body):
        out, session = self._root(body.get("root")), body.get("session")

        def work(job):
            job["steps"], job["step"], job["index"] = ["Lights prüfen"], "Lights prüfen", 1
            res = gallery.check_lights(out, session, lambda i, n, name: job.update(percent=100.0 * i / n,
                                                                                  detail=name))
            job["check"] = {k: res[k] for k in ("n", "main", "fields", "other_field", "first_other")}
            job["check"]["suggest"] = len(res["suggest"])
            self.log("Lights geprüft: %d im Hauptfeld, %d auffällig" % (res["main"], len(res["suggest"])))
        self._start_job("check", "Lights prüfen", body.get("root"), session, work)

    def act_gallery_stack(self, body):
        """Mit Siril oder PixInsight (WBPP) vorverarbeiten und stapeln; danach die Zwischenbilder löschen."""
        out, session = self._root(body.get("root")), body.get("session")
        tool = self._tool(body)
        if tool == "pixinsight":
            return self._stack_pixinsight(out, session, body.get("root"))
        if tool == "rechner":
            return self._stack_rechner(out, session, body.get("root"))

        def work(job):
            res = gallery.prepare_siril(out, session, self._library_for(out))
            job["steps"] = res["steps"]
            t0 = time.time()
            self.log("▶︎ Siril stapelt %d Lights von %s" % (res["lights"], session))
            self._siril(job, res["folder"], res["script"])
            new = [n for n in os.listdir(res["folder"]) if n.startswith("ergebnis_") and gallery.kind_of(n) == "fits"
                   and os.path.getmtime(os.path.join(res["folder"], n)) >= t0 - 1]
            if not new:
                raise RuntimeError("Siril lief durch, aber es gibt kein Ergebnis")
            job["result"] = gallery.rel_of(out, os.path.join(res["folder"], sorted(new)[-1]))
            job["freed"] = gallery.cleanup_siril(res["folder"])
            self.log("✔︎ Siril: %s in %.0f s – Zwischenbilder gelöscht (%.1f GB frei)" % (
                sorted(new)[-1], time.time() - t0, job["freed"] / 1e9))
        self._start_job("stack", "Mit Siril stapeln", body.get("root"), session, work, tool="siril")

    def _stack_pixinsight(self, out, session, root):
        """WBPP ohne Oberfläche: Export nach pixinsight/, Lauf mit Ereignisskript, Master-Light als
        pixinsight/wbpp_<n>x<Zeit>s.xisf, Zwischenbilder weg."""
        exe = gallery.pixinsight_cli()
        if not gallery.wbpp_script(exe):
            raise CameraError("WBPP nicht gefunden – PixInsight 1.9.6 oder neuer nötig")

        def work(job):
            res = gallery.prepare_wbpp(out, session, self._library_for(out), exe)
            job["step"] = "PixInsight startet, WBPP prüft die Bilder"
            t0 = time.time()
            self.log("▶︎ PixInsight (WBPP) stapelt %d Lights von %s" % (res["lights"], session))
            try:
                path = gallery.run_wbpp(res["command"], res["folder"], job)
            except (ValueError, OSError) as e:
                raise RuntimeError(str(e)) from None
            job["result"] = gallery.rel_of(out, path)
            self.log("✔︎ WBPP: %s in %.0f s – Zwischenbilder gelöscht (%.1f GB frei)%s" % (
                os.path.basename(path), time.time() - t0, job["freed"] / 1e9,
                "; Warnung: " + "; ".join(job["warnings"]) if job["warnings"] else ""))
        self._start_job("stack", "Mit PixInsight stapeln", root, session, work, tool="pixinsight")

    def act_gallery_finish(self, body):
        """Gestapeltes Bild mit Siril oder PixInsight fertig entwickeln → JPEG und 16-Bit-TIFF daneben."""
        out, rel = self._root(body.get("root")), body.get("rel")
        tool = self._tool(body)
        if tool == "rechner":
            return self._finish_rechner(out, rel, body.get("root"))
        try:
            res = (gallery.prepare_pi_finish(out, rel, gallery.pixinsight_cli(), gallery.find_model(MODEL_DIRS))
                   if tool == "pixinsight"
                   else gallery.prepare_finish(out, rel))
        except (ValueError, OSError) as e:
            raise CameraError(str(e)) from None
        parts = rel.split("/")
        name = "PixInsight" if tool == "pixinsight" else "Siril"

        def work(job):
            job["steps"] = res["steps"]
            t0 = time.time()
            if tool == "pixinsight":
                done = gallery.run_pi_finish(res["command"], res["outputs"], job, steps=res["steps"])
            else:
                self._siril(job, res["folder"], res["script"])
                os.remove(res["script"])
                done = [p for p in res["outputs"] if os.path.isfile(p) and os.path.getmtime(p) >= t0 - 1]
            if not done:                             # ältere Ergebnisse gleichen Namens zählen nicht
                raise RuntimeError("%s lief durch, aber es gibt kein fertiges Bild" % name)
            job["results"] = [gallery.rel_of(out, p) for p in done]
            job["result"] = job["results"][0]
            self.log("✔︎ Fertig entwickelt (%s): %s" % (name, ", ".join(os.path.basename(p) for p in done)))
        self._start_job("finish", "Mit %s entwickeln" % name, body.get("root"),
                        parts[1] if len(parts) > 2 and parts[0] == "Astro" else None, work, tool=tool)

    # ---------- Rechner: PixInsight auf einem zweiten Rechner (rechner.py, rechner/6d-rechner.py)
    def _rechner_client(self):
        cfg = self.rechner_cfg
        if not cfg["url"] or not cfg["token"]:
            raise CameraError("Rechner nicht eingerichtet – Adresse und Schlüssel unter „Rechner ⚙︎“ eintragen")
        return rechner.Client(cfg["url"], cfg["token"])

    def _root_label(self, out):
        """Ablage für den Namen der Sitzung auf dem Rechner („Kamera“, „Simulator“)."""
        for r in self.gallery_roots():
            if os.path.realpath(r["path"]) == os.path.realpath(out):
                return r["label"]
        return "Fotos"

    def rechner_info(self, max_age=15.0, fresh=False):
        """Einstellungen (ohne Schlüssel) und Zustand des Rechners; die Abfrage wird max_age Sekunden gemerkt –
        ein nicht erreichbarer Rechner kostet sonst bei jedem Öffnen der Bildverwaltung die Wartezeit."""
        cfg = self.rechner_cfg
        info = {"configured": bool(cfg["url"] and cfg["token"]), "url": cfg["url"], "token_set": bool(cfg["token"]),
                "live": bool(cfg["live"]), "ok": False, "error": None,
                "live_state": dict(self.rechner_live) if self.rechner_live else None}
        if not info["configured"]:
            return info
        c = self._rechner_cache
        if fresh or c["url"] != cfg["url"] or time.time() - c["t"] > max_age:
            try:
                c.update(status=rechner.Client(cfg["url"], cfg["token"]).status(timeout=2.5), error=None)
            except rechner.RechnerError as e:
                c.update(status=None, error=str(e))
            c.update(t=time.time(), url=cfg["url"])
        st = c["status"]
        if st:
            info.update(ok=True, name=st.get("name"), pixinsight=bool(st.get("pixinsight")),
                        wbpp=bool(st.get("wbpp")), gpu=st.get("gpu") or [], cpus=st.get("cpus"),
                        ram_gb=st.get("ram_gb"), free_gb=st.get("free_gb"), busy=bool(st.get("job")),
                        mldenoise=st.get("mldenoise"))
        else:
            info["error"] = c["error"]
        return info

    def act_rechner_config(self, body):
        """Adresse, Schlüssel (leer = bleibt) und Vorab-Hochladen speichern, dann den Rechner fragen."""
        try:
            url = rechner.normalize_url(body.get("url"))
        except ValueError as e:
            raise CameraError(str(e)) from None
        cfg = dict(self.rechner_cfg, url=url, live=bool(body.get("live")))
        if body.get("token"):
            cfg["token"] = str(body["token"]).strip()
        if not url:
            cfg["token"] = ""
        try:
            rechner.save_config(cfg)
        except OSError as e:
            raise CameraError("Einstellungen nicht gespeichert: %s" % e) from None
        self.rechner_cfg = cfg
        self._rechner_known.clear()
        info = self.rechner_info(fresh=True)
        state = info["name"] if info["ok"] else info["error"]
        self.log("Rechner: %s" % ("aus" if not url else "%s – %s" % (url, state)))
        return {"rechner": info}

    def act_rechner_check(self, body):
        return {"rechner": self.rechner_info(fresh=True)}

    def _rechner_progress(self, job, verb):
        def up(i, n, sent, total):
            job.update(step="%s hochladen" % verb if n else "%s abgleichen" % verb,
                       percent=100.0 * sent / total if total else 100.0,
                       detail="%d/%d Dateien · %.0f von %.0f MB" % (min(i + 1, n), n, sent / 1e6, total / 1e6)
                       if n else "alles schon auf dem Rechner")
        return up

    @staticmethod
    def _follow(job):
        """Fortschritt des Auftrags auf dem Rechner in den eigenen übernehmen."""
        def update(remote):
            job.update(steps=remote.get("steps") or [], index=remote.get("index") or 0,
                       step=remote.get("step") or "", detail=remote.get("detail") or "",
                       percent=remote.get("percent") or 0.0, warnings=list(remote.get("warnings") or []))
        return update

    def _download(self, client, job, rs, rel, dest):
        job.update(steps=[], index=0, step="Ergebnis laden", percent=0.0, detail=os.path.basename(rel))
        client.get(rs, rel, dest, progress=lambda done, size: job.update(
            percent=100.0 * done / size if size else 0.0,
            detail="%s · %.0f von %.0f MB" % (os.path.basename(rel), done / 1e6, size / 1e6)))
        return dest

    def _stack_rechner(self, out, session, root):
        """Export wie für WBPP, Rohbilder auf den Rechner abgleichen (fehlende hoch, überzählige weg), WBPP dort,
        Ergebnis nach pixinsight/ zurück."""
        client = self._rechner_client()
        rs = rechner.remote_session(self._root_label(out), session)

        def work(job):
            res = gallery.export_pixinsight(out, session, self._library_for(out))
            if not res["lights_raw"]:
                raise RuntimeError("Keine RAW-Lights in %s – WBPP braucht RAWs" % session)
            t0 = time.time()
            job["step"] = "Rohbilder abgleichen"
            with self._rechner_sync_lock:
                st = rechner.sync(client, rs, rechner.export_files(res["input"]),
                                  delete_prefix=gallery.EXPORT_INPUT + "/",
                                  progress=self._rechner_progress(job, "Rohbilder"))
                self._rechner_known.pop(rs, None)
            self.log("▶︎ Rechner: %d Rohbilder hochgeladen (%.0f MB), %d schon dort, %d entfernt – WBPP für %s" % (
                st["uploaded"], st["bytes"] / 1e6, st["kept"], st["deleted"], session))
            remote = client.wait(client.start("wbpp", rs), self._follow(job))
            if remote.get("error"):
                raise RuntimeError("Rechner: %s" % remote["error"])
            dest = self._download(client, job, rs, remote["result"],
                                  os.path.join(res["folder"], os.path.basename(remote["result"])))
            job.update(result=gallery.rel_of(out, dest), freed=remote.get("freed") or 0,
                       warnings=list(remote.get("warnings") or []))
            self.log("✔︎ Rechner: %s in %.0f s (WBPP %.0f s)" % (
                os.path.basename(dest), time.time() - t0, (remote.get("finished") or 0) - (remote.get("started") or 0)))
        self._start_job("stack", "Auf dem Rechner stapeln", root, session, work, tool="rechner")

    def _finish_rechner(self, out, rel, root):
        """Stapel hochladen (wenn er nicht schon oben ist), mit PixInsight dort fertig entwickeln, JPEG und TIFF
        neben den Stapel zurückholen."""
        client = self._rechner_client()
        try:
            path = gallery._finish_source(out, rel)
        except (ValueError, OSError) as e:
            raise CameraError(str(e)) from None
        parts = rel.split("/")
        session = parts[1] if len(parts) > 2 and parts[0] == "Astro" else None
        rs = rechner.remote_session(self._root_label(out), session or "Ergebnisse")
        name = os.path.basename(path)

        def work(job):
            job["step"] = "Stapel abgleichen"
            with self._rechner_sync_lock:
                rechner.sync(client, rs, {name: path}, progress=self._rechner_progress(job, "Stapel"))
            remote = client.wait(client.start("finish", rs, name), self._follow(job))
            if remote.get("error"):
                raise RuntimeError("Rechner: %s" % remote["error"])
            done = [self._download(client, job, rs, r, os.path.join(os.path.dirname(path), os.path.basename(r)))
                    for r in remote["results"]]
            job["results"] = [gallery.rel_of(out, p) for p in done]
            job["result"] = job["results"][0]
            self.log("✔︎ Auf dem Rechner entwickelt: %s" % ", ".join(os.path.basename(p) for p in done))
        self._start_job("finish", "Auf dem Rechner entwickeln", root, session, work, tool="rechner")

    def rechner_live_thread(self):
        while True:
            time.sleep(RECHNER_LIVE_EVERY)
            cfg = self.rechner_cfg
            if cfg["live"] and cfg["url"] and cfg["token"]:
                try:
                    self.rechner_live_pass()
                except (rechner.RechnerError, CameraError, OSError) as e:
                    self.rechner_live = dict(self.rechner_live or {}, error=str(e), time=time.time())

    def rechner_live_pass(self, now=None):
        """Neue Rohbilder der laufenden Sitzungen schon während der Serie hochladen – beim Stapeln fehlen dann nur
        noch die letzten. Nur hochladen, nichts löschen (das macht der Abgleich vor dem Stapeln)."""
        client = self._rechner_client()
        label = self._root_label(OUT)
        state = None
        for session in rechner.recent_sessions(OUT, now=now):
            job = self.gallery_job
            if job and job["active"] and job["tool"] == "rechner" and job["session"] == session:
                continue                             # WBPP liest gerade die Rohbilder – nichts dazwischenschieben
            wanted = rechner.live_files(OUT, session, self.library, now=now)
            if not wanted:
                continue
            rs = rechner.remote_session(label, session)
            known = self._rechner_known.get(rs)
            if known is None:
                known = self._rechner_known[rs] = client.files(rs)
            todo = {r: p for r, p in wanted.items() if (known.get(r) or {}).get("size") != os.path.getsize(p)}
            if todo:
                with self._rechner_sync_lock:
                    st = rechner.sync(client, rs, todo, remote=known)
                self.log("Rechner: %d Rohbilder von %s vorab hochgeladen (%.0f MB)" % (
                    st["uploaded"], session, st["bytes"] / 1e6))
            up = [r for r in wanted if r in known]
            kinds = {}
            for r in up:
                kinds[rechner.frame_label(r)] = kinds.get(rechner.frame_label(r), 0) + 1
            state = {"session": session, "uploaded": len(up), "total": len(wanted), "kinds": kinds,
                     "time": time.time(), "error": None}
        if state:
            self.rechner_live = state
        return state

    def act_library_delete(self, body):
        try:
            entry = self.library.get(body.get("id"))
            if not entry:
                raise ValueError("Master nicht gefunden")
            self.library.delete(entry["id"])
        except (ValueError, OSError) as e:
            raise CameraError(str(e)) from None
        self.log("Master gelöscht: %s" % entry["name"])

    def act_library_build(self, body):
        kind = body.get("kind")
        if kind not in calib.KINDS:
            raise CameraError("Unbekannter Master-Typ")
        problem = imaging.missing()
        if problem:
            raise CameraError("Master erstellen geht nicht: " + problem)
        if self.master_job and self.master_job["active"]:
            raise CameraError("Es wird gerade ein Master erstellt – bitte warten")
        try:
            files = calib.source_files(ASTRO_DIR, body.get("session") or "", calib.KINDS[kind]["source"])
        except ValueError as e:
            raise CameraError(str(e)) from None
        job = self._new_master_job(kind, body.get("session"))
        threading.Thread(target=self._build_masters, args=(job, files), daemon=True,
                         kwargs={"by_exposure": kind != "flat", "source": body.get("session")}).start()

    def _new_master_job(self, kind, source):
        job = {"active": True, "kind": kind, "label": calib.KINDS[kind]["label"], "source": source,
               "phase": "startet", "done": 0, "total": 0, "error": None, "results": [], "started": time.time()}
        with self.lock:
            self.master_job = job
        return job

    def master_from_series(self, s):
        """Nach einer Dark-/Bias-Serie: warten, bis die RAWs geladen sind, dann den Master bauen."""
        job = self._new_master_job(s["type"], os.path.basename(s["session"]))
        job["phase"], job["total"] = "wartet auf Downloads", s["done"]
        idle_since, deadline = None, time.time() + 3 * 3600
        while len(s["files"]) < s["done"] and time.time() < deadline:
            job["done"] = len(s["files"])
            busy = not self.downloads.empty() or self.downloading
            idle_since = None if busy else (idle_since or time.time())
            if idle_since and time.time() - idle_since > 20:
                break                                    # nichts mehr unterwegs (z. B. Kamera nur auf JPEG)
            time.sleep(0.5)
        exposure = s["exposure"] if s["bulb"] else None
        self._build_masters(job, sorted(s["files"]), exposure=exposure, by_exposure=False,
                            source=os.path.basename(s["session"]))

    def _build_masters(self, job, files, exposure=None, by_exposure=True, source=None):
        """Masters bauen (im Hintergrund). Flats: liegen Dark-Flats in derselben Sitzung, aber noch kein Master
        davon, entsteht der zuerst. Dark-Flats: danach die Master-Flats der Sitzung neu – jetzt mit Dark-Flat."""
        try:
            kind = job["kind"]
            if kind == "flat" and source and not any(e["kind"] == "darkflat" and e["source"] == source
                                                     for e in self.library.list()):
                darkflats = calib.source_files(ASTRO_DIR, source, "darkflats")
                if sum(imaging.kind_of(p) == "raw" for p in darkflats) >= calib.MIN_FRAMES:
                    self._build_groups(job, "darkflat", darkflats, None, False, source)
            self._build_groups(job, kind, files, exposure, by_exposure, source)
            if kind == "darkflat" and source:
                self._rebuild_flats(job, source)
            job["phase"] = "fertig"
        except Exception as e:
            job["error"], job["phase"] = str(e), "Fehler"
            self.log("❌ Master-%s: %s" % (job["label"], e))
        finally:
            job["active"] = False

    def _build_groups(self, job, kind, files, exposure, by_exposure, source):
        groups = [g for g in calib.group_frames(files, by_exposure) if len(g[2]) >= calib.MIN_FRAMES]
        if not groups:
            raise ValueError("Zu wenige RAW-Dateien (mindestens %d mit gleicher ISO%s) – Kamera auf RAW stellen?"
                             % (calib.MIN_FRAMES, " und Zeit" if by_exposure else ""))

        def progress(phase, done, total):
            job.update(phase=phase, done=done, total=total)
        for iso, _, frames in groups:
            job["group"] = "%s · ISO %s, %d Bilder" % (calib.KINDS[kind]["label"], iso, len(frames))
            first = os.path.basename(frames[0][0])
            for e in self.library.list():                  # dieselben Bilder schon einmal → Master ersetzen
                if source and e["kind"] == kind and e["source"] == source and e["first"] == first:
                    self.library.delete(e["id"])
                    self.log("Wird neu erstellt: %s" % e["name"])
            entry = calib.build_master(self.library, kind, frames, progress, self.log, exposure, source=source)
            job["results"].append(entry)

    def _rebuild_flats(self, job, session):
        """Nach neuen Dark-Flats: Master-Flats dieser Sitzung neu erstellen – jetzt mit Dark-Flat statt Bias."""
        files = calib.source_files(ASTRO_DIR, session, "flats")
        if sum(imaging.kind_of(p) == "raw" for p in files) >= calib.MIN_FRAMES:
            self.log("Master-Flat der Sitzung wird mit dem neuen Dark-Flat neu erstellt")
            self._build_groups(job, "flat", files, None, False, session)

    # ---------- Download
    def download_worker(self):
        while True:
            handle, transfer, frame = self.downloads.get()
            try:
                self.download(handle, transfer, frame)
            except CameraError as e:
                self.log("❌ Download:", e)
            except Exception as e:
                self.log("❌ Download:", repr(e))
            finally:
                with self.lock:
                    self.downloading = None

    def download(self, handle, transfer, frame=None):
        info = self.submit(GET_OBJECT_INFO, (handle,), name="Bildinfo", timeout=30)
        if info.rc != RC_OK or len(info.result) < 53:
            raise CameraError("Bildinfo nicht lesbar: %s" % rc_text(info.rc))
        size = struct.unpack_from("<I", info.result, 8)[0]
        name, _ = ptp_string(info.result, 52)
        name = os.path.basename(name) or "IMG_%08x" % handle
        stem, ext = os.path.splitext(name)

        if frame:                                    # Serienbild: Namenskonvention + Eintrag in serie.csv
            path = os.path.join(frame["folder"], frame["base"] + ext)
            mode = frame["download"]                 # all | raw | jpeg | none
            load = transfer or mode == "all" or mode == imaging.kind_of(name)   # „nur Computer“: muss geladen werden
            row = {"typ": astro.FRAME_TYPES[frame["type"]]["label"], "nr": frame["index"],
                   "datei": os.path.basename(path), "kameradatei": name,
                   "ordner": astro.FRAME_TYPES[frame["type"]]["folder"], "start": frame["start"],
                   "belichtung_geplant_s": frame["planned"] or "", "belichtung_ist_s": frame["exposure"] or "",
                   "iso": frame["iso"], "blende": frame["aperture"], "zeit": frame["shutter"],
                   "modus": frame["mode"], "geladen": "ja" if load else "nein (auf der Karte)"}
            if not load:
                astro.log_file(frame["session"], row)
                return
        else:
            path = os.path.join(OUT, name)
            if os.path.exists(path):
                path = os.path.join(OUT, "%s_%s%s" % (stem, time.strftime("%H%M%S"), ext))

        with self.lock:
            self.downloading = {"name": os.path.basename(path), "total": size, "got": 0}
        self.log("⬇︎ Lade %s (%.1f MB) …" % (name, size / 1e6))
        t0 = time.time()
        data = self.fetch_object(handle, size, name)
        if transfer and TRANSFER_COMPLETE in self.supported:
            self.submit(TRANSFER_COMPLETE, (handle,), name="Übertragung fertig")
        with open(path, "wb") as f:
            f.write(data)
        if frame:
            astro.log_file(frame["session"], row)
        dt = max(time.time() - t0, 0.1)
        self.log("✔︎ Gespeichert: %s (%.1f MB/s)" % (os.path.basename(path), len(data) / 1e6 / dt))
        self.after_download(path, frame)
        preview = os.path.join(PREVIEW_DIR, os.path.basename(path) + ".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "1800", path, "--out", preview],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.lock:
            self.photos.appendleft({"name": os.path.basename(path), "time": time.strftime("%H:%M:%S"),
                                    "size": len(data),
                                    "preview": os.path.basename(preview) if os.path.exists(preview) else None})

    def after_download(self, path, frame):
        """Neue Datei auf dem Mac: RAWs der Serie merken (Master), Light-Werte merken, Live-Stack füttern."""
        kind = imaging.kind_of(path)
        if frame and kind == "raw":
            frame["files"].append(path)
            if frame["type"] == "flat":
                self._flat_feedback(path, frame)
        if (frame and frame["type"] != "light") or kind not in ("raw", "jpeg"):
            return
        meta = imaging.read_meta(path)
        iso = meta["iso"] or (int(frame["iso"]) if frame and frame["iso"].isdigit() else None)
        exposure = (frame and (frame["exposure"] or frame["planned"])) or meta["exposure"]
        with self.lock:
            last = self.last_light or {}
            self.last_light = {"iso": iso, "exposure": exposure, "time": time.time(),
                               "temp": meta["temp"] if meta["temp"] is not None else last.get("temp"),
                               "fnumber": meta["fnumber"], "focal": meta["focal"], "lens": meta["lens"]}
        live = self.live
        if live and live.active:
            live.add(path, {"exposure": frame["exposure"] if frame else None})

    def _flat_feedback(self, path, frame):
        """Je RAW-Flat: Helligkeit messen (ideal 30–60 %) und ISO/Zeit für passende Dark-Flats merken."""
        meta = imaging.read_meta(path)
        level = None
        if imaging.missing() is None:
            try:
                level = round(calib.flat_level(path), 3)
            except Exception as e:
                self.log("⚠️ Flat-Helligkeit nicht messbar: %r" % e)
        with self.lock:
            s = self.series if self.series and self.series["started"] == frame.get("sid") else None
            exps = s.setdefault("flat_exps", []) if s is not None else []
            if meta["exposure"]:
                exps.append(meta["exposure"])
            if s is not None and level is not None:
                s["flat_level"] = level
            median_exp = sorted(exps)[len(exps) // 2] if exps else meta["exposure"]
            self.last_flat = {"iso": meta["iso"], "exposure": median_exp, "fnumber": meta["fnumber"],
                              "focal": meta["focal"], "level": level, "time": time.time()}
        if level is not None and len(exps) <= 1:                   # beim ersten Flat sofort Bescheid geben
            lo, hi = calib.FLAT_LEVEL
            self.log(("✔︎ Flat-Helligkeit %d %%" if lo <= level <= hi else
                      "⚠️ Flat-Helligkeit %d %% – " + ("zu dunkel: Belichtungskorrektur +1…+2 EV" if level < lo
                                                       else "zu hell: −1 EV oder Lichtquelle dunkler"))
                     % round(100 * level))

    def fetch_object(self, handle, size, name):
        """Bild laden – in Stücken (GetPartialObject), damit Bulb-Befehle dazwischen drankommen.
        Kann die Kamera das nicht, am Stück (GetObject)."""
        if GET_PARTIAL_OBJECT in self.supported and size:
            buf = bytearray()
            while len(buf) < size:
                op = self.submit(GET_PARTIAL_OBJECT, (handle, len(buf), min(DOWNLOAD_CHUNK, size - len(buf))),
                                 name="Download " + name, timeout=60)
                if op.rc != RC_OK or not op.result:
                    if not buf:
                        break                        # Stückweise geht nicht → am Stück versuchen
                    raise CameraError("Download abgebrochen: %s" % rc_text(op.rc))
                buf += op.result
                with self.lock:
                    if self.downloading:
                        self.downloading["got"] = len(buf)
            if len(buf) >= size:
                return bytes(buf[:size])
        op = self.submit(GET_OBJECT, (handle,), name="Download " + name, timeout=900)
        if op.rc != RC_OK or not op.result:
            raise CameraError("Download fehlgeschlagen: %s" % rc_text(op.rc))
        return op.result

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
                dl = dict(self.downloading, got=max(got, self.downloading.get("got", 0)))
            series = None
            if self.series:
                series = {k: v for k, v in self.series.items() if k not in ("session", "files", "flat_exps")}
                series["folder"] = self.series["folder"].replace(os.path.expanduser("~"), "~")
                series["loaded"] = len(self.series["files"])
            fps = 0.0
            if len(self.fps_times) >= 2 and time.time() - self.fps_times[-1] < 2:
                fps = (len(self.fps_times) - 1) / max(self.fps_times[-1] - self.fps_times[0], 0.001)
            return {
                "connected": self.connected, "status": self.status, "model": self.model, "ip": self.cam_ip,
                "connection": self.connection_info(),
                "mode": AE_MODES.get(ae, "?" if ae is None else str(ae)), "bulb_mode": self.is_bulb_mode(),
                "iso": prop(P_ISO, fmt_iso), "aperture": prop(P_APERTURE, fmt_aperture),
                "shutter": prop(P_SHUTTER, fmt_shutter),
                "liveview": {"on": self.liveview, "zoom": self.zoom, "center": self.zoom_center,
                             "fps": round(fps, 1), "seq": self.frame_seq,
                             "can_zoom": ZOOM in self.supported, "can_move": ZOOM_POSITION in self.supported,
                             "can_focus": DRIVE_LENS in self.supported},
                "bulb": dict(self.bulb, active=True, now=time.time()) if self.bulb else {"active": False},
                "autodownload": self.autodownload, "downloading": dl, "photos": list(self.photos),
                "series": series, "now": time.time(),
                "live": self.live.status() if self.live else None,
                "imaging": imaging.missing(), "library_seq": self.library.seq,
                "master_job": dict(self.master_job) if self.master_job else None,
                "last_light": self.last_light, "last_flat": self.last_flat,
                "gallery_job": dict(self.gallery_job) if self.gallery_job else None,
                "rechner_live": dict(self.rechner_live) if self.rechner_live else None,
                "out": OUT, "log": list(self.logs)[-40:],
            }

    def library_info(self):
        return {"masters": self.library.list(), "sources": calib.sources(ASTRO_DIR),
                "folder": LIBRARY_DIR.replace(os.path.expanduser("~"), "~"), "missing": imaging.missing()}


# ---------------------------------------------------------------- Webserver
class Handler(BaseHTTPRequestHandler):
    studio = None
    ACTIONS = {"/api/set": "act_set", "/api/liveview": "act_liveview", "/api/zoom": "act_zoom",
               "/api/zoommove": "act_zoommove", "/api/focus": "act_focus", "/api/bulb/start": "act_bulb_start",
               "/api/bulb/stop": "act_bulb_stop", "/api/shoot": "act_shoot",
               "/api/autodownload": "act_autodownload",
               "/api/series/start": "act_series_start", "/api/series/stop": "act_series_stop",
               "/api/live/start": "act_live_start", "/api/live/stop": "act_live_stop",
               "/api/live/reset": "act_live_reset", "/api/live/view": "act_live_view",
               "/api/live/save": "act_live_save", "/api/live/folder": "act_live_folder",
               "/api/live/analyze": "act_live_analyze",
               "/api/gallery/open": "act_gallery_open", "/api/gallery/adopt": "act_gallery_adopt",
               "/api/gallery/cull": "act_gallery_cull", "/api/gallery/export": "act_gallery_export",
               "/api/gallery/script": "act_gallery_script", "/api/gallery/check": "act_gallery_check",
               "/api/gallery/stack": "act_gallery_stack", "/api/gallery/finish": "act_gallery_finish",
               "/api/rechner/config": "act_rechner_config", "/api/rechner/check": "act_rechner_check",
               "/api/connection": "act_connection",
               "/api/library/delete": "act_library_delete", "/api/library/build": "act_library_build"}

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
        elif u.path == "/api/library":
            self.send_json(st.library_info())
        elif u.path == "/api/gallery":
            self.send_json(st.gallery_info())
        elif u.path in ("/api/gallery/items", "/api/gallery/thumb", "/api/gallery/session"):
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                out = st._root(q.get("root"))
                if u.path == "/api/gallery/items":
                    self.send_json({"items": gallery.items(out, q.get("source", ""), q.get("group", ""))})
                elif u.path == "/api/gallery/session":
                    self.send_json(st.gallery_session(q.get("root"), q.get("session")))
                else:
                    size = min(2400, max(64, int(q.get("size", gallery.THUMB))))
                    data, ctype = gallery.thumbnail(out, q.get("rel", ""), size)
                    self.send_bytes(data, ctype)
            except (ValueError, OSError, CameraError) as e:
                self.send_json({"error": str(e)}, 404)
        elif u.path == "/api/live.png":
            live, png, seq = st.live, None, 0
            if live:
                with live.lock:
                    png, seq = live.png, live.seq
            if png:
                self.send_bytes(png, "image/png", extra={"X-Stack-Seq": str(seq)})
            else:
                self.send_response(204)
                self.end_headers()
        elif u.path == "/api/live/saved.png":
            path = st.live.saved[-1] if st.live and st.live.saved else None
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    self.send_bytes(f.read(), "image/png")
            else:
                self.send_json({"error": "noch nichts gespeichert"}, 404)
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
            result = getattr(self.studio, method)(body)
            self.send_json(dict({"ok": True}, **(result if isinstance(result, dict) else {})))
        except CameraError as e:
            self.send_json({"ok": False, "error": str(e)})
        except Exception as e:
            self.studio.log("❌ Fehler bei %s: %r" % (u.path, e))
            self.send_json({"ok": False, "error": "Interner Fehler: %r" % e})


def main():
    try:
        guid = load_guid()
    except (OSError, SystemExit):
        guid = None                                  # nur das Kabel geht; WLAN meldet „zuerst koppeln“
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
    threading.Thread(target=studio.rechner_live_thread, daemon=True).start()
    studio.log("6D Studio läuft – Bedienoberfläche: http://127.0.0.1:%d  (Fotos: %s)" % (WEB_PORT, OUT))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
