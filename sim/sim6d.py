#!/usr/bin/env python3
"""Simulierte Canon EOS 6D – spricht PTP/IP wie die echte Kamera im Modus „Fernsteuerung (EOS Utility)“.

Damit lässt sich 6D Studio ohne Kamera und ohne Hotspot entwickeln und testen. Nachgebildet werden auch
die Eigenheiten der echten 6D (siehe docs/canon-6d-ptpip.md):
  * die Anmeldung dauert ~4 s                                  (--init-delay)
  * nach CloseSession lehnt sie alles ab, nach dem Trennen endet die Fernsteuerung
    (Port zu, bis sie „neu gestartet“ wird)                    (--restart-delay)
  * ohne Abfragen trennt sie nach 30 s                         (--idle-timeout)
  * Abschaltautomatik mit Vorwarnung 0xC18D                    (--apo)
  * langsamer Download wie über den iPhone-Hotspot             (--mbps)

Livebild: künstlicher Sternenhimmel, der auf die Lupe (Zoom/ZoomPosition) und den Fokusantrieb (DriveLens)
reagiert. Aufnahmen landen als JPEG „auf der Karte“ und werden mit ObjectAddedEx (0xC181) gemeldet.

Aufruf:   sim/sim6d.py [--port 15741] [--mode B|M] [--init-delay 4] …
Studio dagegen starten:  CAM_PORT=15741 STUDIO_PORT=8067 GPHOTO_PORT=15742 studio/6d-studio.py 127.0.0.1
(oder einfach: 6d-fernsteuerung sim).  Tastatur-Steuerung im Terminal: help
"""
import argparse
import math
import os
import random
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

# ---------------------------------------------------------------- Protokoll (wie studio/6d-studio.py)
INIT_CMD_REQ, INIT_CMD_ACK, INIT_EVT_REQ, INIT_EVT_ACK, INIT_FAIL = 1, 2, 3, 4, 5
OP_REQ, OP_RESP, START_DATA, DATA, END_DATA = 6, 7, 9, 10, 12

OK, GENERAL_ERROR, SESSION_NOT_OPEN, NOT_SUPPORTED = 0x2001, 0x2002, 0x2003, 0x2005
BUSY, INVALID_VALUE, SESSION_OPEN, NOT_READY = 0x2019, 0x201C, 0x201E, 0xA102

P_APERTURE, P_SHUTTER, P_ISO, P_AEMODE = 0xD101, 0xD102, 0xD103, 0xD105
P_DEST, P_EVF_OUT, P_EVF_MODE = 0xD11C, 0xD1B0, 0xD1B1
E_PROP, E_AVAIL, E_OBJECT_ADDED, E_STORAGE_INFO, E_WILL_SHUTDOWN = 0xC189, 0xC18A, 0xC181, 0xC185, 0xC18D
MODE_M, MODE_B = 3, 4
SHUTTER_BULB = 0x0C

# Befehle laut GetDeviceInfo der echten 6D (Auszug) – ohne BulbStart/BulbEnd, wie bei der 6D
OPS_6D = [0x1001, 0x1002, 0x1003, 0x1004, 0x1005, 0x1007, 0x1008, 0x1009, 0x100A, 0x101B,
          0x910F, 0x9110, 0x9114, 0x9115, 0x9116, 0x9117, 0x911D, 0x9128, 0x9129, 0x9153, 0x9155, 0x9158, 0x9159]
CAM_GUID = bytes.fromhex("00000000000000000100" + "2c9efcca9f95")

SENSOR_W, SENSOR_H = 5472, 3648
LV_W, LV_H = 960, 640
PHOTO_W, PHOTO_H = 1824, 1216


def third_steps(first, last):
    out, v = [], first
    while v <= last:
        out += [x for x in (v, v + 3, v + 5) if x <= last]
        v += 8
    return out


ISO_CODES = [0, 0x40] + third_steps(0x48, 0x88) + [0x90, 0x98]       # Auto, L, 100–25600, H1, H2
APERTURE_CODES = third_steps(0x10, 0x48)                               # f/1.4–f/16
SHUTTER_CODES = third_steps(0x10, 0x98)                                # 30" – 1/4000


def iso_value(v):
    return 100 * 2 ** ((v - 0x48) / 8) if v else 800


def f_number(v):
    return 2 ** ((v - 8) / 16)


def shutter_seconds(v):
    return 2 ** (-(v - 0x38) / 8)


# ---------------------------------------------------------------- Sternenhimmel
def make_stars(seed=6):
    rnd = random.Random(seed)
    stars = [(rnd.uniform(0, SENSOR_W), rnd.uniform(0, SENSOR_H), 1 + 6 * rnd.random() ** 0.6) for _ in range(900)]
    stars.append((SENSOR_W / 2 + 140, SENSOR_H / 2 - 90, 0.3))   # heller Stern nahe der Mitte zum Scharfstellen
    return stars


def render(w, h, region, stars, sigma_sensor, gain=1.0, bg=14):
    """Graustufenbild w×h vom Sensorausschnitt region = (x0, y0, breite, höhe)."""
    x0, y0, rw, _ = region
    scale = rw / w                                   # Sensorpixel pro Bildpixel
    buf = bytearray()
    for y in range(h):                               # leichter Himmelsverlauf (Lichtverschmutzung unten)
        buf += bytes([min(255, int(bg + 10 * y / h))]) * w
    sigma = max(0.6, sigma_sensor / scale)
    r = int(3 * sigma) + 1
    g = [math.exp(-(d * d) / (2 * sigma * sigma)) for d in range(-r, r + 1)]
    dim = min(1.0, 1.4 / sigma)                      # unscharfe Sterne verteilen ihr Licht
    for sx, sy, mag in stars:
        vx, vy = (sx - x0) / scale, (sy - y0) / scale
        if vx < -r or vy < -r or vx >= w + r or vy >= h + r:
            continue
        amp = gain * 400 * 10 ** (-0.4 * (mag - 1)) * dim
        if amp < 2:
            continue
        cx, cy = int(vx), int(vy)
        for j in range(-r, r + 1):
            yy = cy + j
            gy = g[j + r] * amp
            if yy < 0 or yy >= h or gy < 1:
                continue
            row = yy * w
            for i in range(-r, r + 1):
                xx = cx + i
                if 0 <= xx < w:
                    v = int(gy * g[i + r])
                    if v:
                        p = row + xx
                        nv = buf[p] + v
                        buf[p] = 255 if nv > 255 else nv
    return buf


def to_jpeg(w, h, buf, quality=80):
    """8-Bit-Graustufen → BMP → JPEG (über sips, das macOS mitbringt)."""
    rows = b"".join(bytes(buf[y * w:(y + 1) * w]) for y in range(h - 1, -1, -1))   # w ist durch 4 teilbar
    off = 14 + 40 + 1024
    bmp = (b"BM" + struct.pack("<IHHI", off + len(rows), 0, 0, off)
           + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 8, 0, len(rows), 2835, 2835, 256, 0)
           + b"".join(bytes((i, i, i, 0)) for i in range(256)) + rows)
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "f.bmp"), os.path.join(d, "f.jpg")
        with open(src, "wb") as f:
            f.write(bmp)
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality), src, "--out", dst],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(dst, "rb") as f:
            return f.read()


# ---------------------------------------------------------------- Hilfen
def rec(typ, payload=b""):
    return struct.pack("<II", 8 + len(payload), typ) + payload


def ptp_string(s):
    s += "\0"
    return bytes([len(s)]) + s.encode("utf-16-le")


def ptype(pkt):
    return struct.unpack_from("<I", pkt, 4)[0]


def recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Gegenstelle hat getrennt")
        buf += chunk
    return bytes(buf)


def recv_packet(s):
    hdr = recv_exact(s, 8)
    length = struct.unpack_from("<I", hdr)[0]
    return hdr + (recv_exact(s, length - 8) if length > 8 else b"")


# ---------------------------------------------------------------- Kamera
class Camera:
    def __init__(self, args):
        self.args = args
        self.lock = threading.RLock()
        self.props = {P_ISO: 0x70, P_APERTURE: 0x10, P_SHUTTER: 0x6D,
                      P_AEMODE: MODE_B if args.mode == "B" else MODE_M,
                      P_DEST: 4, P_EVF_OUT: 0, P_EVF_MODE: 0}
        self.events = []
        self.stars = make_stars()
        self.frames = {}
        self.objects = {}
        self.next_handle, self.next_img = 0x9001, 9901
        self.exposure = None
        self.focus = args.focus
        self.zoom = 1
        self.zoom_pos = (int(SENSOR_W * 2 / 5), int(SENSOR_H * 2 / 5))   # 5×-Rahmen in der Mitte
        self.reset_remote()
        self.ended_until = 0.0
        self.frozen = False

    def log(self, *a):
        if not self.args.quiet:
            print(time.strftime("%H:%M:%S"), "[6D-Sim]", *a, flush=True)

    def reset_remote(self):
        self.session = None              # None | "open" | "closed"
        self.remote_mode = self.event_mode = False
        self.props[P_EVF_OUT] = 0
        self.zoom = 1
        self.last_keepon = time.time()
        self.apo_warned = False

    # ---------- Zustand
    def allowed(self, prop):
        if prop == P_ISO:
            return ISO_CODES
        if prop == P_APERTURE:
            return APERTURE_CODES
        if prop == P_SHUTTER:
            return [] if self.props[P_AEMODE] == MODE_B else SHUTTER_CODES
        if prop == P_DEST:
            return [1, 4]
        return None

    def prop_event(self, prop):
        return rec(E_PROP, struct.pack("<II", prop, self.props[prop]))

    def avail_event(self, prop):
        vals = self.allowed(prop)
        return rec(E_AVAIL, struct.pack("<III", prop, 3, len(vals)) + b"".join(struct.pack("<I", v) for v in vals))

    def full_dump(self):
        return [self.prop_event(p) for p in self.props] + \
               [self.avail_event(p) for p in (P_ISO, P_APERTURE, P_SHUTTER, P_DEST)]

    def set_mode(self, mode):
        with self.lock:
            self.props[P_AEMODE] = MODE_B if mode == "B" else MODE_M
            self.events += [self.prop_event(P_AEMODE), self.avail_event(P_SHUTTER), self.prop_event(P_SHUTTER)]
        self.log("Moduswahlrad auf", mode)

    def sigma(self):
        return 1.4 + 0.12 * abs(self.focus)          # Sterngröße in Sensorpixeln

    def zoom_region(self):
        if self.zoom == 1:
            return (0, 0, SENSOR_W, SENSOR_H)
        w, h = SENSOR_W / self.zoom, SENSOR_H / self.zoom
        x = min(max(self.zoom_pos[0], 0), SENSOR_W - w)
        y = min(max(self.zoom_pos[1], 0), SENSOR_H - h)
        return (x, y, w, h)

    def liveview_jpeg(self):
        key = (self.zoom, self.zoom_region(), self.focus)
        if key not in self.frames:
            if len(self.frames) > 64:
                self.frames.clear()
            self.frames[key] = to_jpeg(LV_W, LV_H, render(LV_W, LV_H, self.zoom_region(), self.stars, self.sigma()))
        return self.frames[key]

    # ---------- Aufnahme
    def release_on(self):
        if self.exposure:
            return BUSY
        if self.props[P_AEMODE] == MODE_B:
            self.exposure = {"start": time.time(), "bulb": True}
            self.log("● Bulb-Belichtung läuft")
        else:
            t = shutter_seconds(self.props[P_SHUTTER])
            self.exposure = {"start": time.time(), "bulb": False}
            self.log("● Belichtung %.2f s" % t)
            timer = threading.Timer(t, self.end_exposure, args=(t,))
            timer.daemon = True
            timer.start()
        return OK

    def release_off(self):
        exp = self.exposure
        if exp and exp["bulb"]:
            self.end_exposure(time.time() - exp["start"])
        return OK

    def end_exposure(self, seconds):
        self.exposure = None
        self.log("■ Belichtung beendet nach %.1f s – speichere Bild" % seconds)
        threading.Thread(target=self.store_photo, args=(seconds,), daemon=True).start()

    def store_photo(self, seconds):
        p = self.props
        gain = min(8.0, max(0.15, seconds * iso_value(p[P_ISO]) / 3200 * (2.8 / f_number(p[P_APERTURE])) ** 2))
        buf = render(PHOTO_W, PHOTO_H, (0, 0, SENSOR_W, SENSOR_H), self.stars, self.sigma(),
                     gain=gain, bg=min(140, 8 + 8 * gain))
        jpeg = to_jpeg(PHOTO_W, PHOTO_H, buf, 90)
        with self.lock:
            handle, name = self.next_handle, "IMG_%04d.JPG" % self.next_img
            self.next_handle += 1
            self.next_img += 1
            self.objects[handle] = (name, jpeg)
            nm = name.encode() + b"\0"
            payload = bytearray(0x20 + len(nm))      # ObjectAddedEx: Handle @8, Speicher @0x0c, Format @0x10,
            struct.pack_into("<IIH", payload, 0, handle, 0x00010001, 0x3801)      # Größe @0x1c, Name @0x28
            struct.pack_into("<II", payload, 0x14, len(jpeg), 0x90000000)
            payload[0x20:] = nm
            self.events += [rec(E_OBJECT_ADDED, bytes(payload)), rec(E_STORAGE_INFO, struct.pack("<I", 0x00010001))]
        self.log("Bild %s (%.1f MB) auf der Karte" % (name, len(jpeg) / 1e6))

    def object_info(self, handle):
        name, data = self.objects[handle]
        return (struct.pack("<IHHIHIIIIIIIHII", 0x00010001, 0x3801, 0, len(data), 0, 0, 0, 0,
                            PHOTO_W, PHOTO_H, 8, 0x90000000, 0, 0, 0)
                + ptp_string(name) + ptp_string("20261117T230000") + ptp_string("20261117T230000") + ptp_string(""))

    def device_info(self):
        def arr(v):
            return struct.pack("<I", len(v)) + b"".join(struct.pack("<H", x) for x in v)
        return (struct.pack("<HIH", 100, 0xB, 100) + ptp_string("") + struct.pack("<H", 0)
                + arr(OPS_6D) + arr([0xC189, 0xC18A, 0xC181]) + arr(list(self.props)) + arr([]) + arr([0x3801])
                + ptp_string("Canon Inc.") + ptp_string("Canon EOS 6D") + ptp_string("3-1.1.9") + ptp_string("SIM0001"))

    # ---------- Befehle
    def handle(self, code, params, data):
        """→ (Antwortcode, Daten an den Rechner oder None, Antwortparameter)."""
        with self.lock:
            if self.session == "closed":
                return NOT_SUPPORTED, None, ()                       # wie die echte 6D nach CloseSession
            if code == 0x1001:
                return OK, self.device_info(), ()
            if code == 0x1002:
                if self.session == "open":
                    return SESSION_OPEN, None, ()
                self.session = "open"
                return OK, None, ()
            if code == 0x1003:
                self.session = "closed"
                self.log("CloseSession – ab jetzt wird alles abgelehnt (wie bei der echten 6D)")
                return OK, None, ()
            if self.session != "open":
                return SESSION_NOT_OPEN, None, ()
            evf = self.props[P_EVF_OUT] == 2

            if code == 0x9114:
                self.remote_mode = bool(params and params[0])
                return OK, None, ()
            if code == 0x9115:
                self.event_mode = bool(params and params[0])
                if self.event_mode:
                    self.events += self.full_dump()
                return OK, None, ()
            if code == 0x9116:
                self.check_apo()
                out, self.events = b"".join(self.events) + rec(0), []
                return OK, out, ()
            if code == 0x911D:
                self.last_keepon, self.apo_warned = time.time(), False
                return OK, None, ()
            if code == 0x9110:
                if len(data) < 12:
                    return INVALID_VALUE, None, ()
                _, prop, value = struct.unpack_from("<III", data)
                return self.set_prop(prop, value), None, ()
            if code == 0x9153:
                if not evf or self.exposure:
                    return NOT_READY, None, ()
                jpeg = self.liveview_jpeg()
                return OK, struct.pack("<II", 8 + len(jpeg), 1) + jpeg + struct.pack("<II", 16, 4) + b"\0" * 8, ()
            if code == 0x9158:
                if not evf:
                    return NOT_READY, None, ()
                if not params or params[0] not in (1, 5, 10):
                    return INVALID_VALUE, None, ()
                self.zoom = params[0]
                self.log("Lupe %d×" % self.zoom)
                return OK, None, ()
            if code == 0x9159:
                if not evf:
                    return NOT_READY, None, ()
                self.zoom_pos = (params[0], params[1])
                return OK, None, ()
            if code == 0x9155:
                if not evf:
                    return NOT_READY, None, ()
                step = {1: 1, 2: 8, 3: 40}.get(params[0] & 0xFF, 0) if params else 0
                self.focus += step if params[0] & 0x8000 else -step
                self.log("Fokus %+d → Abstand zur Schärfe %d" % (step if params[0] & 0x8000 else -step, self.focus))
                return OK, None, ()
            if code == 0x9128:
                return self.release_on(), None, ()
            if code == 0x9129:
                return self.release_off(), None, ()
            if code == 0x910F:
                if self.props[P_AEMODE] == MODE_B:
                    return OK, None, (3,)                            # löst im Modus B nicht aus
                return self.release_on(), None, (0,)
            if code in (0x1008, 0x1009):
                if not params or params[0] not in self.objects:
                    return 0x2009, None, ()                          # InvalidObjectHandle
                if code == 0x1008:
                    return OK, self.object_info(params[0]), ()
                return OK, self.objects[params[0]][1], ()
            if code == 0x9117:
                return OK, None, ()
            return NOT_SUPPORTED, None, ()

    def set_prop(self, prop, value):
        allowed = self.allowed(prop)
        if allowed is not None and value not in allowed:
            return INVALID_VALUE
        self.props[prop] = value
        if prop == P_EVF_OUT:
            if value != 2:
                self.zoom = 1
            self.log("Livebild", "an" if value == 2 else "aus")
        self.events.append(self.prop_event(prop))
        return OK

    def check_apo(self):
        apo = self.args.apo
        if not apo:
            return
        idle = time.time() - self.last_keepon
        if idle > apo - 10 and not self.apo_warned:
            self.apo_warned = True
            self.events.append(rec(E_WILL_SHUTDOWN))
            self.log("Abschaltautomatik: Vorwarnung 0xC18D gesendet")
        if idle > apo:
            self.log("Abschaltautomatik – Kamera schläft und antwortet nicht mehr")
            self.frozen = True


# ---------------------------------------------------------------- Netzwerk
class Server:
    def __init__(self, cam, args):
        self.cam, self.args = cam, args
        self.port = None
        self.cmd_sock = None
        self.evt_socks = []
        self.lst = None
        self.running = True
        self.ready = threading.Event()

    def start(self):
        threading.Thread(target=self.serve, daemon=True).start()
        self.ready.wait(5)

    def open_listener(self):
        lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        lst.bind((self.args.host, self.port if self.port is not None else self.args.port))
        lst.listen(4)
        lst.settimeout(0.3)
        self.port = lst.getsockname()[1]
        return lst

    def serve(self):
        self.lst = self.open_listener()
        self.cam.log("Simulierte EOS 6D wartet auf %s:%d (Fernsteuerung bereit)" % (self.args.host, self.port))
        self.ready.set()
        while self.running:
            if time.time() < self.cam.ended_until:
                if self.lst:                      # Fernsteuerung beendet: Port zu wie bei der echten Kamera
                    self.lst.close()
                    self.lst = None
                time.sleep(0.1)
                continue
            if not self.lst:
                self.lst = self.open_listener()
                self.cam.log("Fernsteuerung (EOS Utility) neu gestartet – Port offen")
            try:
                c, _ = self.lst.accept()
            except socket.timeout:
                continue
            except OSError:
                continue
            threading.Thread(target=self.client, args=(c,), daemon=True).start()

    def stop(self):
        self.running = False
        self.drop()
        if self.lst:
            self.lst.close()

    def drop(self):
        s = self.cmd_sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def client(self, s):
        try:
            s.settimeout(10)
            pkt = recv_packet(s)
        except (OSError, ConnectionError):
            s.close()
            return
        if ptype(pkt) == INIT_CMD_REQ:
            self.command_channel(s, pkt)
        elif ptype(pkt) == INIT_EVT_REQ:
            s.sendall(struct.pack("<II", 8, INIT_EVT_ACK))
            self.evt_socks.append(s)
        else:
            s.close()

    def command_channel(self, s, pkt):
        cam, args = self.cam, self.args
        name = pkt[24:-4].decode("utf-16-le", "replace").rstrip("\0")
        if self.cmd_sock:
            s.sendall(struct.pack("<III", 12, INIT_FAIL, 2))            # schon verbunden
            s.close()
            return
        if args.reject:
            time.sleep(0.3)
            cam.log("Anmeldung von „%s“ abgelehnt (InitFail 0x1)" % name)
            s.sendall(struct.pack("<III", 12, INIT_FAIL, 1))
            s.close()
            return
        cam.log("Anmeldung von „%s“ – antworte nach %.1f s (wie die echte 6D)" % (name, args.init_delay))
        time.sleep(args.init_delay)
        nm = "6D\0".encode("utf-16-le")
        s.sendall(struct.pack("<III", 8 + 4 + 16 + len(nm) + 4, INIT_CMD_ACK, 1) + CAM_GUID + nm
                  + struct.pack("<HH", 0, 1))
        self.cmd_sock = s
        s.settimeout(args.idle_timeout or None)
        try:
            while True:
                pkt = recv_packet(s)
                if ptype(pkt) != OP_REQ:
                    continue
                phase, code, tid = struct.unpack_from("<IHI", pkt, 8)
                n = (len(pkt) - 18) // 4
                params = struct.unpack_from("<%dI" % n, pkt, 18) if n > 0 else ()
                data = b""
                if phase == 2:                                           # Daten vom Rechner
                    while True:
                        d = recv_packet(s)
                        if ptype(d) in (DATA, END_DATA):
                            data += d[12:]
                        if ptype(d) == END_DATA:
                            break
                while cam.frozen:                                        # schläft: keine Antwort
                    time.sleep(0.2)
                if args.verbose:
                    cam.log("Befehl 0x%04x %s" % (code, [hex(p) for p in params]))
                rc, out, rparams = cam.handle(code, params, data)
                if out is not None:
                    self.send_data(s, tid, out, throttle=(code == 0x1009))
                body = struct.pack("<HI", rc, tid) + b"".join(struct.pack("<I", p) for p in rparams)
                s.sendall(struct.pack("<II", 8 + len(body), OP_RESP) + body)
        except socket.timeout:
            cam.log("Keine Befehle seit %d s – trenne (wie die echte 6D)" % args.idle_timeout)
        except (ConnectionError, OSError):
            cam.log("Rechner hat getrennt")
        finally:
            self.cmd_sock = None
            for e in self.evt_socks + [s]:
                try:
                    e.close()
                except OSError:
                    pass
            self.evt_socks = []
            with cam.lock:
                cam.reset_remote()
                cam.frozen = False
                cam.ended_until = time.time() + args.restart_delay
            cam.log("Fernsteuerung beendet – startet in %.0f s neu" % args.restart_delay)

    def send_data(self, s, tid, data, throttle=False):
        s.sendall(struct.pack("<IIIQ", 20, START_DATA, tid, len(data)))
        chunk = 128 * 1024
        for i in range(0, max(len(data), 1), chunk):
            part = data[i:i + chunk]
            last = i + chunk >= len(data)
            s.sendall(struct.pack("<III", 12 + len(part), END_DATA if last else DATA, tid) + part)
            if throttle and self.args.mbps:
                time.sleep(len(part) / (self.args.mbps * 1e6))


# ---------------------------------------------------------------- Tastatur-Steuerung
HELP = """Befehle:
  mode B | mode M   Moduswahlrad stellen
  drop              Verbindung trennen (wie WLAN-Abbruch)
  freeze / wake     Kamera antwortet nicht mehr / wieder
  restart           Fernsteuerung sofort wieder bereit
  focus N           Abstand zur Schärfe setzen (0 = scharf)
  apo N             Abschaltautomatik nach N s ohne „wach halten“ (0 = aus)
  status            Zustand anzeigen"""


def console(cam, server):
    for line in sys.stdin:
        cmd = line.split()
        if not cmd:
            continue
        c = cmd[0].lower()
        if c == "mode" and len(cmd) > 1:
            cam.set_mode(cmd[1].upper())
        elif c == "drop":
            server.drop()
        elif c == "freeze":
            cam.frozen = True
            cam.log("Kamera eingefroren")
        elif c == "wake":
            cam.frozen = False
            cam.log("Kamera wieder wach")
        elif c == "restart":
            cam.ended_until = 0
        elif c == "focus" and len(cmd) > 1:
            cam.focus = int(cmd[1])
            cam.log("Fokus-Abstand", cam.focus)
        elif c == "apo" and len(cmd) > 1:
            cam.args.apo = float(cmd[1])
            cam.log("Abschaltautomatik", cam.args.apo or "aus")
        elif c == "status":
            cam.log("verbunden=%s Session=%s Modus=%s Livebild=%s Lupe=%d× Fokus=%d Bilder=%d" % (
                bool(server.cmd_sock), cam.session, "B" if cam.props[P_AEMODE] == MODE_B else "M",
                cam.props[P_EVF_OUT] == 2, cam.zoom, cam.focus, len(cam.objects)))
        else:
            print(HELP, flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Simulierte Canon EOS 6D (PTP/IP)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=15741)
    p.add_argument("--mode", choices=["B", "M"], default="B", help="Moduswahlrad beim Start")
    p.add_argument("--init-delay", type=float, default=4.0, help="Sekunden bis zur Anmeldebestätigung")
    p.add_argument("--idle-timeout", type=float, default=30, help="trennt ohne Befehle nach N s (0 = nie)")
    p.add_argument("--restart-delay", type=float, default=3, help="Port zu nach dem Trennen für N s")
    p.add_argument("--mbps", type=float, default=1.0, help="Download-Tempo in MB/s (0 = unbegrenzt)")
    p.add_argument("--apo", type=float, default=0, help="Abschaltautomatik nach N s (0 = aus)")
    p.add_argument("--focus", type=int, default=60, help="Abstand zur Schärfe beim Start")
    p.add_argument("--reject", action="store_true", help="jede Anmeldung ablehnen (InitFail 0x1)")
    p.add_argument("-v", "--verbose", action="store_true", help="jeden Befehl protokollieren")
    p.add_argument("-q", "--quiet", action="store_true")
    return p.parse_args(argv)


def main():
    args = parse_args()
    cam = Camera(args)
    server = Server(cam, args)
    server.start()
    if sys.stdin and sys.stdin.isatty():
        print(HELP, flush=True)
        threading.Thread(target=console, args=(cam, server), daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
