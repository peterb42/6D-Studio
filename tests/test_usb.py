"""Tests für das USB-Kabel (studio/ptpusb.py) und das Umschalten WLAN ↔ Kabel – ohne Kamera.

Ein Ersatzgerät (tests/fake_usb.py) spricht auf den „Bulk-Endpunkten“ PTP wie die 6D und führt die Befehle mit
dem Simulator aus (sim6d.Camera.handle). So läuft das ganze Studio über die Brücke.
Ausführen:  .venv/bin/python -m unittest tests.test_usb -v
"""
import importlib.util
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "studio"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ["OUT"] = tempfile.mkdtemp(prefix="6d-usb-test-")             # keine Testdateien in ~/Pictures
os.environ["GPHOTO_DIR"] = tempfile.mkdtemp(prefix="6d-usb-gphoto-")    # nicht die echte Verbindungsart ändern
import fake_usb  # noqa: E402
import ptpusb  # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, [rel]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


studio = load("studio_ueber_kabel", "studio/6d-studio.py")
sim = load("sim6d_kabel", "sim/sim6d.py")


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def recv_packet(s):
    hdr = b""
    while len(hdr) < 8:
        hdr += s.recv(8 - len(hdr))
    length = struct.unpack_from("<I", hdr)[0]
    rest = b""
    while len(rest) < length - 8:
        rest += s.recv(length - 8 - len(rest))
    return hdr + rest


def op_req(code, tid, *params, phase=1):
    b = struct.pack("<IHI", phase, code, tid) + b"".join(struct.pack("<I", p) for p in params)
    return struct.pack("<II", 8 + len(b), ptpusb.OP_REQ) + b


class TestContainer(unittest.TestCase):
    """PTP-Container auf den Endpunkten: Daten in beide Richtungen, Nullpakete, Reste."""

    def test_daten_von_der_kamera_auch_an_paketgrenzen(self):
        for size in (0, 500, 3 * 512 - 12, (1 << 20) - 12, 2_000_000):          # 1536 und 1 MiB: mit Nullpaket
            payload = bytes(range(256)) * (size // 256) + bytes(size % 256)
            dev = fake_usb.FakeUsbDevice(lambda code, params, data, p=payload: (0x2001, p, (7,)))
            ptp = ptpusb.PtpUsb(dev)
            for tid in (5, 6):                                               # die zweite liest kein Nullpaket mit
                rc, rparams, data = ptp.transaction(0x1009, tid, (1,))
                self.assertEqual((rc, rparams, data), (0x2001, (7,), payload), size)

    def test_daten_zur_kamera(self):
        got = []
        dev = fake_usb.FakeUsbDevice(lambda code, params, data: got.append((code, params, data)) or (0x2001, None, ()))
        ptp = ptpusb.PtpUsb(dev)
        prop = struct.pack("<III", 12, 0xD103, 0x58)
        self.assertEqual(ptp.transaction(0x9110, 3, (), prop), (0x2001, (), None))
        self.assertEqual(got, [(0x9110, (), prop)])
        big = os.urandom(4 * 512 - 12)                                       # Container genau 2048 Bytes
        ptp.transaction(0x9110, 4, (), big)
        self.assertEqual(got[-1][2], big)

    def test_libusb_transfers_und_fehler(self):
        """UsbDevice gegen eine libusb zum Nachsehen: Nullpaket an Paketgrenzen, Lesen, Fehlercodes."""
        class Lib:
            def __init__(self):
                self.calls, self.rc, self.data = [], 0, b"\x01\x02\x03"

            def libusb_bulk_transfer(self, handle, ep, buf, n, done, ms):
                self.calls.append((ep, n))
                if ep & 0x80:
                    ctypes.memmove(buf, self.data, len(self.data))
                    done._obj.value = len(self.data)
                else:
                    done._obj.value = n
                return self.rc

            def libusb_error_name(self, rc):
                return b"LIBUSB_ERROR_IO"

            def libusb_clear_halt(self, handle, ep):
                self.calls.append(("halt", ep))
        import ctypes
        lib = Lib()
        dev = ptpusb.UsbDevice(lib, None, None, 0, 0x81, 0x02, 512, "Test")
        dev.write(b"x" * 2048, 1)
        dev.write(b"x" * 100, 1)
        self.assertEqual(lib.calls, [(0x02, 2048), (0x02, 0), (0x02, 100)])
        self.assertEqual(dev.read(1 << 20, 1), b"\x01\x02\x03")
        for rc, err in ((-7, ptpusb.UsbTimeout), (-4, ptpusb.UsbError), (-1, ptpusb.UsbError)):
            lib.rc, lib.data = rc, b""
            with self.assertRaises(err):
                dev.read(512, 1)
        lib.rc = -9                                                          # Endpunkt hängt: freimachen
        with self.assertRaises(ptpusb.UsbError):
            dev.read(512, 1)
        self.assertEqual(lib.calls[-1], ("halt", 0x81))

    def test_reste_und_alte_antworten(self):
        dev = fake_usb.FakeUsbDevice(lambda *a: (0x2001, None, ()), zlp=False)
        ptp = ptpusb.PtpUsb(dev)
        old = struct.pack("<IHHI", 12, 3, 0x2019, 41)                        # Antwort einer abgebrochenen Transaktion
        data = struct.pack("<IHHI", 12 + 4, 2, 0x1001, 42) + b"abcd"
        resp = struct.pack("<IHHI", 16, 3, 0x2001, 42) + struct.pack("<I", 9)
        dev.outbox = [old, data + resp]                                      # zwei Container in einem Transfer
        dev.command = None
        dev.handler = lambda *a: (_ for _ in ()).throw(AssertionError("darf nicht ausgeführt werden"))
        dev._run_pending = lambda: None
        self.assertEqual(ptp.transaction(0x1001, 42), (0x2001, (9,), b"abcd"))

    def test_bruecke_wie_ptpip(self):
        got = []

        def handler(code, params, data):
            got.append((code, params, data))
            return (0x2001, b"INFO" * 300, ()) if code == 0x1001 else (0x2001, None, (3,))
        dev = fake_usb.FakeUsbDevice(handler)
        bridge = ptpusb.Bridge(ptpusb.PtpUsb(dev))
        self.addCleanup(bridge.cmd.close)
        self.addCleanup(bridge.evt.close)
        c = bridge.cmd
        c.settimeout(5)
        c.sendall(op_req(0x1001, 1))
        start, end, resp = recv_packet(c), recv_packet(c), recv_packet(c)
        self.assertEqual(struct.unpack_from("<IIIQ", start)[1:], (ptpusb.START_DATA, 1, 1200))
        self.assertEqual((struct.unpack_from("<I", end, 4)[0], end[12:]), (ptpusb.END_DATA, b"INFO" * 300))
        self.assertEqual(struct.unpack_from("<IHI", resp, 4), (ptpusb.OP_RESP, 0x2001, 1))
        prop = struct.pack("<III", 12, 0xD103, 0x58)                         # SetProp: Daten zur Kamera
        c.sendall(op_req(0x9110, 2, phase=2) + struct.pack("<IIIQ", 20, ptpusb.START_DATA, 2, len(prop))
                  + struct.pack("<III", 12 + len(prop), ptpusb.END_DATA, 2) + prop)
        resp = recv_packet(c)
        self.assertEqual(struct.unpack_from("<IHII", resp, 4), (ptpusb.OP_RESP, 0x2001, 2, 3))
        self.assertEqual(got[-1], (0x9110, (), prop))
        bridge.close()                                                       # Studio trennt: Kabel frei
        self.assertTrue(bridge.done.wait(5))
        self.assertTrue(dev.closed)

    def test_kabel_weg(self):
        dev = fake_usb.FakeUsbDevice(lambda *a: (0x2001, None, ()))
        bridge = ptpusb.Bridge(ptpusb.PtpUsb(dev), log=lambda *a: None)
        self.addCleanup(bridge.cmd.close)
        self.addCleanup(bridge.evt.close)
        dev.closed = True
        bridge.cmd.sendall(op_req(0x9116, 1))
        bridge.cmd.settimeout(5)
        self.assertEqual(bridge.cmd.recv(100), b"")                         # das Studio sieht das Ende
        self.assertTrue(bridge.done.wait(5))

    def test_anmeldung_fuer_gphoto2(self):
        cmd_ack, evt_ack = ptpusb.init_acks()
        self.assertEqual(struct.unpack_from("<III", cmd_ack), (len(cmd_ack), ptpusb.INIT_CMD_ACK, 1))
        self.assertEqual(evt_ack, struct.pack("<II", 8, ptpusb.INIT_EVT_ACK))


@unittest.skipUnless(sys.platform == "darwin", "braucht sips (macOS)")
class TestStudioUeberKabel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        args = sim.parse_args(["--port", "0", "--init-delay", "0", "--mbps", "0", "--quiet"])
        cls.cam = sim.Camera(args)
        studio.GPHOTO_PORT = 0
        cls.st = studio.Studio()
        cls.st.conn_mode = "usb"
        cls.devices, cls.logs, cls.unplugged = [], [], False

        def open_fake(log=None):
            if cls.unplugged:
                raise ptpusb.UsbError("Keine Canon am USB-Kabel – Kamera an, Kabel drin, Wi-Fi an der Kamera aus?")
            d = fake_usb.FakeUsbDevice(cls.cam.handle)
            cls.devices.append(d)
            return d
        cls.st.usb_open = open_fake
        cls.st.log = lambda *a: cls.logs.append(" ".join(str(x) for x in a))
        threading.Thread(target=cls.st.camera_thread, args=(None, "test"), daemon=True).start()
        threading.Thread(target=cls.st.download_worker, daemon=True).start()
        if not wait_for(lambda: cls.st.connected and studio.P_ISO in cls.st.props, 15):
            raise RuntimeError("Studio verbindet sich nicht über das Ersatzkabel:\n" + "\n".join(cls.logs))

    def test_1_verbunden_und_einstellungen(self):
        st = self.st
        self.assertEqual(st.model, "Canon EOS 6D")
        self.assertEqual(st.snapshot()["connection"]["mode"], "usb")
        self.assertTrue(any("Canon EOS 6D, USB" in line for line in self.logs))
        st.act_set({"prop": "iso", "value": 0x58})
        self.assertEqual(self.cam.props[sim.P_ISO], 0x58)
        self.assertTrue(wait_for(lambda: st.props[studio.P_ISO] == 0x58, 3))

    def test_2_livebild(self):
        st = self.st
        st.act_liveview({"on": True})
        seq = st.frame_seq
        self.assertTrue(wait_for(lambda: st.frame_seq > seq + 2, 5))
        self.assertTrue(st.frame.startswith(b"\xff\xd8"))
        st.act_liveview({"on": False})

    def test_3_bulb_mit_download(self):
        st = self.st
        n = len(st.photos)
        st.act_bulb_start({"seconds": 0})
        time.sleep(0.5)
        st.act_bulb_stop({})
        self.assertTrue(wait_for(lambda: len(st.photos) > n, 20), "\n".join(self.logs[-10:]))
        self.assertTrue(os.path.isfile(os.path.join(studio.OUT, st.photos[0]["name"])))

    def test_4_gphoto2_ueber_das_studio(self):
        c = socket.create_connection(("127.0.0.1", self.st.gphoto_port), timeout=10)
        self.addCleanup(c.close)
        body = b"\x22" * 16 + "gphoto\0".encode("utf-16-le") + struct.pack("<HH", 0, 1)
        c.sendall(struct.pack("<II", 8 + len(body), 1) + body)
        self.assertEqual(struct.unpack_from("<I", recv_packet(c), 4)[0], studio.INIT_CMD_ACK)
        c.sendall(op_req(0x1001, 7))
        while True:
            p = recv_packet(c)
            if struct.unpack_from("<I", p, 4)[0] == ptpusb.OP_RESP:
                self.assertEqual(struct.unpack_from("<HI", p, 8), (0x2001, 7))
                break

    def test_5_kabel_ab_und_wieder_dran(self):
        st = self.st
        n = len(self.devices)
        type(self).unplugged = True
        self.devices[-1].closed = True                                       # Kabel gezogen
        self.cam.reset_remote()
        self.assertTrue(wait_for(lambda: not st.connected, 10))
        self.assertTrue(wait_for(lambda: "Keine Canon am USB-Kabel" in st.status, 10), st.status)
        type(self).unplugged = False                                         # wieder eingesteckt
        self.assertTrue(wait_for(lambda: st.connected and studio.P_ISO in st.props, 15), "\n".join(self.logs[-8:]))
        self.assertEqual(len(self.devices), n + 1)

    def test_6_umschalten(self):
        st = self.st
        with self.assertRaises(studio.CameraError):
            st.act_connection({"mode": "bluetooth"})
        with self.assertRaises(studio.CameraError):
            st.act_connection({"mode": "wlan", "ip": "kamera"})
        st.act_bulb_start({"seconds": 0})                                    # während der Belichtung: nein
        with self.assertRaisesRegex(studio.CameraError, "Belichtung"):
            st.act_connection({"mode": "wlan"})
        st.act_bulb_stop({})
        res = st.act_connection({"mode": "wlan", "ip": "127.0.0.1"})["connection"]
        self.assertEqual((res["mode"], res["ip"]), ("wlan", "127.0.0.1"))
        with open(studio.MODE_FILE) as f:
            self.assertEqual(f.read().strip(), "wlan")
        with open(studio.IPFILE) as f:
            self.assertEqual(f.read().strip(), "127.0.0.1")
        self.assertTrue(wait_for(lambda: not st.connected, 10))
        self.assertTrue(wait_for(lambda: self.devices[-1].closed, 10))     # Kabel freigegeben
        self.assertTrue(wait_for(lambda: any("WLAN-Kopplung" in line for line in self.logs), 10))
        n = len(self.devices)
        st.act_connection({"mode": "usb"})                                   # und zurück aufs Kabel
        self.assertTrue(wait_for(lambda: st.connected, 15), "\n".join(self.logs[-8:]))
        self.assertEqual(len(self.devices), n + 1)
        with open(studio.MODE_FILE) as f:
            self.assertEqual(f.read().strip(), "usb")


if __name__ == "__main__":
    unittest.main()
