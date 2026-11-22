"""Ersatz für die 6D am USB-Kabel: verhält sich auf den Bulk-Endpunkten wie ein PTP-Gerät (Container, Nullpaket an
Paketgrenzen) und führt die Befehle mit einer Funktion aus – in den Tests mit dem Simulator (sim6d.Camera.handle).

Schnittstelle wie ptpusb.UsbDevice: write(Bytes, s), read(Größe, s), close(), mps, where.
"""
import struct
import threading

import ptpusb


class FakeUsbDevice:
    def __init__(self, handler, mps=512, zlp=True, where="Testgerät"):
        self.handler, self.mps, self.zlp, self.where = handler, mps, zlp, where
        self.inbuf = bytearray()
        self.outbox = []                 # Segmente: je ein Container (oder b"" für ein Nullpaket)
        self.command = None              # (Code, Transaktion, Parameter, Daten) – wartet auf Daten oder Abfrage
        self.closed = False
        self.reads, self.writes = 0, []
        self.lock = threading.Lock()

    # Rechner → Kamera
    def write(self, data, timeout):
        if self.closed:
            raise ptpusb.UsbError("Kamera nicht mehr am Kabel")
        with self.lock:
            self.writes.append(len(data))
            self.inbuf += data
            while len(self.inbuf) >= 12:
                length, ctype, code, tid = struct.unpack_from("<IHHI", self.inbuf)
                if len(self.inbuf) < length:
                    break
                body = bytes(self.inbuf[12:length])
                del self.inbuf[:length]
                if ctype == ptpusb.CONTAINER_COMMAND:
                    self._run_pending()
                    n = len(body) // 4
                    self.command = [code, tid, struct.unpack_from("<%dI" % n, body) if n else (), None]
                elif ctype == ptpusb.CONTAINER_DATA and self.command and self.command[1] == tid:
                    self.command[3] = body
                    self._run_pending()

    def _run_pending(self):
        if not self.command:
            return
        code, tid, params, data = self.command
        self.command = None
        rc, out, rparams = self.handler(code, params, data or b"")
        if out is not None:
            container = struct.pack("<IHHI", 12 + len(out), ptpusb.CONTAINER_DATA, code, tid) + out
            self.outbox.append(container)
            if self.zlp and len(container) % self.mps == 0:
                self.outbox.append(b"")
        self.outbox.append(struct.pack("<IHHI", 12 + 4 * len(rparams), ptpusb.CONTAINER_RESPONSE, rc, tid)
                           + b"".join(struct.pack("<I", p) for p in rparams))

    # Kamera → Rechner
    def read(self, size, timeout):
        if self.closed:
            raise ptpusb.UsbError("Kamera nicht mehr am Kabel")
        with self.lock:
            self.reads += 1
            if not self.outbox:
                self._run_pending()                      # Befehl ohne Daten: jetzt ausführen
            if not self.outbox:
                raise ptpusb.UsbTimeout("Kamera antwortet nicht (lesen)")
            seg = self.outbox[0]
            part = seg[:size]
            if len(seg) > size:
                self.outbox[0] = seg[size:]
            else:
                self.outbox.pop(0)
            return part

    def close(self):
        self.closed = True
