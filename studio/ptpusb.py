"""PTP über USB-Kabel für 6D Studio – Standardbibliothek plus libusb (Homebrew; kommt mit gphoto2 mit).

Das Studio spricht PTP/IP wie über WLAN. Mit Kabel sitzt eine Brücke dazwischen (Bridge): Für das Studio sieht sie
aus wie Befehls- und Ereignis-Socket der Kamera (socketpair), sie nimmt PTP/IP-Pakete an, führt jede Transaktion
über USB aus und schreibt die Antwort als PTP/IP-Pakete zurück. So laufen Livebild, Bulb, Serien, Downloads und der
gphoto2-Zugang des Studios ohne Änderung über das Kabel.

PTP über USB („USB Still Image Capture Device Definition“, Interface-Klasse 6): auf den Bulk-Endpunkten Container
    Länge (4) · Typ (2: 1 Befehl, 2 Daten, 3 Antwort, 4 Ereignis) · Code (2) · Transaktion (4) · Parameter/Daten
Ist ein Container ein Vielfaches der Paketgröße lang, folgt ein Paket der Länge 0 (ZLP).

Canon meldet Ereignisse über GetEvent (0x9116) – der Interrupt-Endpunkt wird nicht gebraucht.
Auf dem Mac hält der Systemdienst ptpcamerad (Digitale Bilder, Fotos) die Kamera; er wird dann beendet.
"""
import ctypes
import ctypes.util
import os
import socket
import struct
import subprocess
import sys
import threading
import time

CANON = 0x04A9
USB_CLASS_IMAGE = 6
CONTAINER_COMMAND, CONTAINER_DATA, CONTAINER_RESPONSE, CONTAINER_EVENT = 1, 2, 3, 4
CHUNK = 1 << 20                      # Bytes je Bulk-Transfer
READ_TIMEOUT = 30.0                  # s je Lesevorgang (der Wachhund des Studios greift früher)
# PTP/IP-Pakettypen (wie in 6d-studio.py)
INIT_CMD_ACK, INIT_EVT_ACK = 2, 4
OP_REQ, OP_RESP, EVENT, START_DATA, DATA, CANCEL, END_DATA = 6, 7, 8, 9, 10, 11, 12
LIBUSB_PATHS = ("/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib",
                "/opt/local/lib/libusb-1.0.dylib")
ERROR_TIMEOUT, ERROR_BUSY, ERROR_ACCESS, ERROR_NO_DEVICE, ERROR_PIPE = -7, -6, -3, -4, -9


class UsbError(Exception):
    """Fehler auf dem Kabel – das Studio trennt und verbindet neu."""


class UsbMissing(UsbError):
    """libusb fehlt."""


class UsbTimeout(UsbError):
    pass


# ---------------------------------------------------------------- libusb über ctypes
c_uint8, c_uint16, c_int, c_void_p = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_int, ctypes.c_void_p


class _DeviceDescriptor(ctypes.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("bcdUSB", c_uint16),
                ("bDeviceClass", c_uint8), ("bDeviceSubClass", c_uint8), ("bDeviceProtocol", c_uint8),
                ("bMaxPacketSize0", c_uint8), ("idVendor", c_uint16), ("idProduct", c_uint16),
                ("bcdDevice", c_uint16), ("iManufacturer", c_uint8), ("iProduct", c_uint8),
                ("iSerialNumber", c_uint8), ("bNumConfigurations", c_uint8)]


class _EndpointDescriptor(ctypes.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("bEndpointAddress", c_uint8),
                ("bmAttributes", c_uint8), ("wMaxPacketSize", c_uint16), ("bInterval", c_uint8),
                ("bRefresh", c_uint8), ("bSynchAddress", c_uint8), ("extra", c_void_p), ("extra_length", c_int)]


class _InterfaceDescriptor(ctypes.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("bInterfaceNumber", c_uint8),
                ("bAlternateSetting", c_uint8), ("bNumEndpoints", c_uint8), ("bInterfaceClass", c_uint8),
                ("bInterfaceSubClass", c_uint8), ("bInterfaceProtocol", c_uint8), ("iInterface", c_uint8),
                ("endpoint", ctypes.POINTER(_EndpointDescriptor)), ("extra", c_void_p), ("extra_length", c_int)]


class _Interface(ctypes.Structure):
    _fields_ = [("altsetting", ctypes.POINTER(_InterfaceDescriptor)), ("num_altsetting", c_int)]


class _ConfigDescriptor(ctypes.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("wTotalLength", c_uint16),
                ("bNumInterfaces", c_uint8), ("bConfigurationValue", c_uint8), ("iConfiguration", c_uint8),
                ("bmAttributes", c_uint8), ("MaxPower", c_uint8), ("interface", ctypes.POINTER(_Interface)),
                ("extra", c_void_p), ("extra_length", c_int)]


_lib = None
_lib_lock = threading.Lock()


def libusb():
    """libusb-1.0 laden (einmal). → ctypes-Bibliothek; UsbMissing, wenn nicht installiert."""
    global _lib
    with _lib_lock:
        if _lib:
            return _lib
        paths = [p for p in LIBUSB_PATHS if os.path.exists(p)] + [ctypes.util.find_library("usb-1.0")]
        lib = None
        for p in paths:
            if p:
                try:
                    lib = ctypes.CDLL(p)
                    break
                except OSError:
                    continue
        if lib is None:
            raise UsbMissing("libusb fehlt – einmalig: brew install libusb (kommt auch mit gphoto2)")
        P, PP = ctypes.POINTER, ctypes.POINTER(ctypes.POINTER(c_void_p))
        sig = {"libusb_init": (c_int, [P(c_void_p)]), "libusb_exit": (None, [c_void_p]),
               "libusb_get_device_list": (ctypes.c_ssize_t, [c_void_p, PP]),
               "libusb_free_device_list": (None, [P(c_void_p), c_int]),
               "libusb_get_device_descriptor": (c_int, [c_void_p, P(_DeviceDescriptor)]),
               "libusb_get_active_config_descriptor": (c_int, [c_void_p, P(P(_ConfigDescriptor))]),
               "libusb_get_config_descriptor": (c_int, [c_void_p, c_uint8, P(P(_ConfigDescriptor))]),
               "libusb_free_config_descriptor": (None, [P(_ConfigDescriptor)]),
               "libusb_open": (c_int, [c_void_p, P(c_void_p)]), "libusb_close": (None, [c_void_p]),
               "libusb_claim_interface": (c_int, [c_void_p, c_int]),
               "libusb_release_interface": (c_int, [c_void_p, c_int]),
               "libusb_set_auto_detach_kernel_driver": (c_int, [c_void_p, c_int]),
               "libusb_bulk_transfer": (c_int, [c_void_p, ctypes.c_ubyte, P(ctypes.c_ubyte), c_int, P(c_int),
                                                ctypes.c_uint]),
               "libusb_clear_halt": (c_int, [c_void_p, ctypes.c_ubyte]),
               "libusb_get_bus_number": (c_uint8, [c_void_p]), "libusb_get_device_address": (c_uint8, [c_void_p]),
               "libusb_error_name": (ctypes.c_char_p, [c_int])}
        for name, (res, args) in sig.items():
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = res, args
        _lib = lib
        return lib


def missing():
    """None, wenn USB geht – sonst ein Hinweis für die Oberfläche."""
    try:
        libusb()
        return None
    except UsbMissing as e:
        return str(e)


def _err(lib, rc):
    return "%s (%d)" % (lib.libusb_error_name(rc).decode(), rc)


def free_from_macos(log=None):
    """macOS: ptpcamerad beenden – der Dienst öffnet jede angesteckte Kamera und hält sie fest (wie
    `6d-fernsteuerung` für gphoto2). Er startet bei Bedarf von selbst wieder."""
    if sys.platform != "darwin":
        return False
    r = subprocess.run(["killall", "ptpcamerad", "PTPCamera"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode == 0 and log:
        log("macOS-Dienst ptpcamerad beendet – er hielt die Kamera fest")
    return r.returncode == 0


class UsbDevice:
    """Eine geöffnete Kamera: Interface der Klasse 6 mit Bulk-Endpunkten."""

    def __init__(self, lib, ctx, handle, interface, ep_in, ep_out, mps, where):
        self.lib, self.ctx, self.handle, self.interface = lib, ctx, handle, interface
        self.ep_in, self.ep_out, self.mps, self.where = ep_in, ep_out, mps, where
        self._buf = (ctypes.c_ubyte * CHUNK)()
        self._done = c_int()
        self.closed = False

    def _check(self, rc, what):
        if rc == ERROR_TIMEOUT:
            raise UsbTimeout("Kamera antwortet nicht (%s)" % what)
        if rc == ERROR_NO_DEVICE:
            raise UsbError("Kamera nicht mehr am Kabel")
        if rc == ERROR_PIPE:
            self.lib.libusb_clear_halt(self.handle, self.ep_in if what == "lesen" else self.ep_out)
        if rc:
            raise UsbError("USB %s: %s" % (what, _err(self.lib, rc)))

    def write(self, data, timeout):
        ms = int(timeout * 1000)
        view, off = memoryview(data), 0
        while off < len(data):
            n = min(CHUNK, len(data) - off)
            buf = (ctypes.c_ubyte * n).from_buffer_copy(view[off:off + n])
            self._check(self.lib.libusb_bulk_transfer(self.handle, self.ep_out, buf, n, ctypes.byref(self._done), ms),
                        "schreiben")
            off += self._done.value
        if len(data) % self.mps == 0:                 # Container endet genau an einer Paketgrenze: Nullpaket
            self._check(self.lib.libusb_bulk_transfer(self.handle, self.ep_out, self._buf, 0, ctypes.byref(self._done),
                                                      ms), "schreiben")

    def read(self, size, timeout):
        """→ Bytes bis zum Ende des Transfers (kurzes Paket); b"" für ein Nullpaket."""
        size = min(size, CHUNK)
        rc = self.lib.libusb_bulk_transfer(self.handle, self.ep_in, self._buf, size, ctypes.byref(self._done),
                                           int(timeout * 1000))
        if rc == ERROR_TIMEOUT and self._done.value:
            rc = 0
        self._check(rc, "lesen")
        return ctypes.string_at(self._buf, self._done.value)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.lib.libusb_release_interface(self.handle, self.interface)
        self.lib.libusb_close(self.handle)
        self.lib.libusb_exit(self.ctx)


def _find(lib, ctx, vendor):
    """Erstes Gerät des Herstellers mit PTP-Interface → (Gerät, Interface, Bulk in, Bulk out, Paketgröße, Ort)."""
    devs = ctypes.POINTER(c_void_p)()
    n = lib.libusb_get_device_list(ctx, ctypes.byref(devs))
    if n < 0:
        raise UsbError("USB-Geräte nicht lesbar: %s" % _err(lib, n))
    try:
        for i in range(n):
            dev = devs[i]
            desc = _DeviceDescriptor()
            if lib.libusb_get_device_descriptor(dev, ctypes.byref(desc)) or desc.idVendor != vendor:
                continue
            cfg = ctypes.POINTER(_ConfigDescriptor)()
            if lib.libusb_get_active_config_descriptor(dev, ctypes.byref(cfg)) and \
                    lib.libusb_get_config_descriptor(dev, 0, ctypes.byref(cfg)):
                continue
            try:
                c = cfg.contents
                for k in range(c.bNumInterfaces):
                    itf = c.interface[k]
                    for a in range(itf.num_altsetting):
                        alt = itf.altsetting[a]
                        if alt.bInterfaceClass != USB_CLASS_IMAGE:
                            continue
                        ep_in = ep_out = None
                        mps = 512
                        for e in range(alt.bNumEndpoints):
                            ep = alt.endpoint[e]
                            if ep.bmAttributes & 3 == 2:                     # Bulk
                                if ep.bEndpointAddress & 0x80:
                                    ep_in, mps = ep.bEndpointAddress, ep.wMaxPacketSize or 512
                                else:
                                    ep_out = ep.bEndpointAddress
                        if ep_in and ep_out:
                            where = "Bus %d, Gerät %d" % (lib.libusb_get_bus_number(dev),
                                                          lib.libusb_get_device_address(dev))
                            return dev, alt.bInterfaceNumber, ep_in, ep_out, mps, where, devs
            finally:
                lib.libusb_free_config_descriptor(cfg)
    except Exception:
        lib.libusb_free_device_list(devs, 1)
        raise
    lib.libusb_free_device_list(devs, 1)
    return None


def open_camera(vendor=CANON, log=None):
    """Kamera am Kabel öffnen und ihr PTP-Interface belegen. → UsbDevice; UsbError, wenn keine da oder belegt."""
    lib = libusb()
    ctx = c_void_p()
    rc = lib.libusb_init(ctypes.byref(ctx))
    if rc:
        raise UsbError("libusb startet nicht: %s" % _err(lib, rc))
    try:
        found = _find(lib, ctx, vendor)
        if not found:
            raise UsbError("Keine Canon am USB-Kabel – Kamera an, Kabel drin, Wi-Fi an der Kamera aus?")
        dev, interface, ep_in, ep_out, mps, where, devs = found
        handle = c_void_p()
        rc = lib.libusb_open(dev, ctypes.byref(handle))
        lib.libusb_free_device_list(devs, 1)                 # das geöffnete Gerät behält seine Referenz
        if rc:
            raise UsbError("Kamera lässt sich nicht öffnen: %s" % _err(lib, rc))
        lib.libusb_set_auto_detach_kernel_driver(handle, 1)  # Linux; auf dem Mac ohne Wirkung
        rc = lib.libusb_claim_interface(handle, interface)
        if rc in (ERROR_ACCESS, ERROR_BUSY) and free_from_macos(log):
            time.sleep(1.0)
            rc = lib.libusb_claim_interface(handle, interface)
        if rc:
            lib.libusb_close(handle)
            raise UsbError("Kamera ist belegt (%s) – läuft gphoto2, EOS Utility oder Fotos?" % _err(lib, rc))
        return UsbDevice(lib, ctx, handle, interface, ep_in, ep_out, mps, where)
    except Exception:
        lib.libusb_exit(ctx)
        raise


# ---------------------------------------------------------------- PTP-Transaktionen
class PtpUsb:
    """PTP-Container über ein Gerät mit write(Bytes, s), read(Größe, s), close() und mps (Paketgröße)."""

    def __init__(self, dev, log=None):
        self.dev, self.log = dev, log or (lambda *a: None)
        self.pending = b""
        self.drain()

    def drain(self):
        """Reste einer früheren Verbindung verwerfen (abgebrochene Transaktion)."""
        for _ in range(20):
            try:
                if not self.dev.read(CHUNK, 0.05):
                    continue
            except UsbTimeout:
                return

    def _read(self, timeout):
        if self.pending:
            data, self.pending = self.pending, b""
            return data
        return self.dev.read(CHUNK, timeout)

    def read_container(self, timeout=READ_TIMEOUT):
        buf = bytearray()
        while len(buf) < 12:
            buf += self._read(timeout)                       # Nullpakete (b"") zwischen Containern übergehen
        length = struct.unpack_from("<I", buf)[0]
        if length < 12:
            raise UsbError("ungültiger PTP-Container (Länge %d)" % length)
        while len(buf) < length:
            part = self._read(timeout)
            if not part and len(buf) % self.dev.mps:
                raise UsbError("PTP-Container unvollständig: %d von %d Bytes" % (len(buf), length))
            buf += part
        if len(buf) > length:                                # schon der Anfang des nächsten Containers
            self.pending = bytes(buf[length:])
            del buf[length:]
        return bytes(buf)

    def transaction(self, code, tid, params=(), data=None, timeout=READ_TIMEOUT):
        """→ (Antwortcode, Antwortparameter, Daten von der Kamera oder None)."""
        self.dev.write(struct.pack("<IHHI", 12 + 4 * len(params), CONTAINER_COMMAND, code, tid)
                       + b"".join(struct.pack("<I", p) for p in params), timeout)
        if data is not None:
            self.dev.write(struct.pack("<IHHI", 12 + len(data), CONTAINER_DATA, code, tid) + data, timeout)
        payload = None
        while True:
            c = self.read_container(timeout)
            length, ctype, ccode, ctid = struct.unpack_from("<IHHI", c)
            if ctid != tid and ctype in (CONTAINER_DATA, CONTAINER_RESPONSE):
                self.log("USB: Rest einer früheren Transaktion verworfen (0x%04x)" % ccode)
                continue
            if ctype == CONTAINER_DATA:
                payload = (payload or b"") + c[12:]
            elif ctype == CONTAINER_RESPONSE:
                n = (length - 12) // 4
                return ccode, struct.unpack_from("<%dI" % n, c, 12) if n > 0 else (), payload

    def close(self):
        self.dev.close()


# ---------------------------------------------------------------- Brücke PTP/IP ↔ USB
def _recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(min(n - len(buf), CHUNK))
        if not chunk:
            raise ConnectionError("Studio-Seite geschlossen")
        buf += chunk
    return bytes(buf)


def _recv_packet(s):
    hdr = _recv_exact(s, 8)
    length = struct.unpack_from("<I", hdr)[0]
    return hdr + (_recv_exact(s, length - 8) if length > 8 else b"")


def init_acks(name="6D"):
    """Anmeldeantworten für gphoto2 (PTP/IP), wenn das Studio über das Kabel verbunden ist."""
    nm = (name + "\0").encode("utf-16-le")
    cmd_ack = struct.pack("<III", 8 + 4 + 16 + len(nm) + 4, INIT_CMD_ACK, 1) + b"\0" * 16 + nm + \
        struct.pack("<HH", 0, 1)
    return cmd_ack, struct.pack("<II", 8, INIT_EVT_ACK)


class Bridge:
    """Für das Studio: cmd und evt wie die PTP/IP-Sockets zur Kamera. Ein Faden führt jede Anfrage über USB aus.
    Schließt das Studio cmd (Trennen, Umschalten), endet der Faden und gibt das Kabel frei; bricht das Kabel weg,
    schließt der Faden seine Seite – das Studio sieht dann das Ende der Verbindung wie bei WLAN."""

    def __init__(self, ptp, log=None):
        self.ptp, self.log = ptp, log or (lambda *a: None)
        self.cmd, self._cmd = socket.socketpair()
        self.evt, self._evt = socket.socketpair()
        self._out = None                                     # Anfrage, die noch auf ihre Daten wartet
        self.done = threading.Event()
        threading.Thread(target=self._serve, daemon=True, name="usb-bruecke").start()

    def _serve(self):
        try:
            while True:
                pkt = _recv_packet(self._cmd)
                ty = struct.unpack_from("<I", pkt, 4)[0]
                if ty == OP_REQ:
                    phase, code, tid = struct.unpack_from("<IHI", pkt, 8)
                    n = (len(pkt) - 18) // 4
                    params = struct.unpack_from("<%dI" % n, pkt, 18) if n > 0 else ()
                    if phase == 2:
                        self._out = (code, tid, params, bytearray())
                    else:
                        self._run(code, tid, params, None)
                elif ty in (DATA, END_DATA) and self._out:
                    self._out[3].extend(pkt[12:])
                    if ty == END_DATA:
                        code, tid, params, data = self._out
                        self._out = None
                        self._run(code, tid, params, bytes(data))
        except (ConnectionError, OSError):
            pass                                             # Studio hat getrennt
        except UsbError as e:
            self.log("❌ USB: %s" % e)
        finally:
            self._close_inner()

    def _run(self, code, tid, params, data):
        rc, rparams, payload = self.ptp.transaction(code, tid, params, data)
        out = []
        if payload is not None:
            out.append(struct.pack("<IIIQ", 20, START_DATA, tid, len(payload)))
            out.append(struct.pack("<III", 12 + len(payload), END_DATA, tid) + payload)
        body = struct.pack("<HI", rc, tid) + b"".join(struct.pack("<I", p) for p in rparams)
        out.append(struct.pack("<II", 8 + len(body), OP_RESP) + body)
        self._cmd.sendall(b"".join(out))

    def _close_inner(self):
        for s in (self._cmd, self._evt):
            try:
                s.close()
            except OSError:
                pass
        try:
            self.ptp.close()                                 # im eigenen Faden – nie während eines Transfers
        except UsbError:
            pass
        self.done.set()

    def close(self):
        """Von außen: die Studio-Seite schließen; der Faden gibt das Kabel danach selbst frei."""
        for s in (self.cmd, self.evt):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
