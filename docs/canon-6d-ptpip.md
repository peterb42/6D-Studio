# Canon EOS 6D über WLAN (PTP/IP) – Erkenntnisse

Alles hier wurde am 17.11.2026 an einer echten EOS 6D (Firmware 1.1.9) beobachtet
oder durch Versuche bestätigt. Ungeprüftes ist als **offen** markiert.

## Netzwerk

- Die 6D funkt nur im **2,4-GHz-Band**. Öffentliche bzw. offene WLANs trennen Geräte meist voneinander → unbrauchbar.
- Bewährt: **iPhone-Hotspot** mit „Maximale Kompatibilität“ (2,4 GHz). Mac und Kamera sind beide drin, der Mac behält Internet.
  Typische Adressen: iPhone `172.20.10.1`, Mac `172.20.10.2`, Kamera `172.20.10.3`.
- Kamera-Menü: Wi-Fi-Funktion → **Fernsteuerung (EOS Utility)** → „Netzwerk wählen“ (nicht „Einfache Verbindung“,
  sonst wäre der Mac im Kamera-WLAN ohne Internet).
- MAC-Präfix der Kamera: `2c:9e:fc` (Canon).

## Erkennung (Bonjour/mDNS)

- Dienst `_ptp._tcp`, Name `ICPO-WFTEOSSystemService<mac>`, Host `CWC<mac>.local`, Port **15740**.
- TXT-Einträge u. a.: `mn.canon.com=Canon EOS 6D`, `sid.canon.com=…`, `usbid.canon.com=3250`,
  `tid.canon.com=00000000-0000-0000-0001-FFFFFFFFFFFF` (= Kamera wartet auf Kopplung / kein Rechner eingetragen).
- Der Dienst taucht nur kurz auf und verschwindet wieder (Add/Rmv) – Erkennung muss schnell reagieren.

## Anmeldung und Kopplung

- PTP/IP-Standard: `InitCommandRequest` (GUID 16 Byte + Rechnername UTF-16 + Version 1.0) → `InitCommandAck`.
- **Die 6D antwortet erst nach ~4 s.** libgphoto2 wartet nur 2,5 s (`PTPIP_DEFAULT_TIMEOUT_S 2` + 500 ms, nicht
  einstellbar) → deshalb der Vermittler in `6d-studio`.
- `InitFail` mit Grund `0x1` = Rechner (GUID) abgelehnt.
- **Niemals Port 15740 mit einer leeren TCP-Verbindung testen** (z. B. `nc -z`): Die Kamera wertet das als
  gescheiterte Kopplung und beendet den Fernsteuermodus.
- Unsere Kennung steht in `~/.gphoto/settings` als `ptp2_ip=guid=xx:xx:…` (Rohbytes in Sendereihenfolge).
  Diese GUID ist an der Kamera gekoppelt (Rechnername „Proteus“).
- EOS Utility hat eine eigene GUID (`com.canon.EOS-Utility`, Schlüssel `EOS-initiatorGUID`); damit lehnte die 6D
  die Anmeldung in beiden Byte-Reihenfolgen ab.
- Der EOS-Utility-Helfer in der Menüleiste startet beim Anmelden automatisch und kann sich die Kamera schnappen → beenden.

## Session und Wachhalten

- Es gibt genau **eine** PTP-Session. Nach `CloseSession` lehnt die 6D alle Befehle ab („Operation Not Supported“);
  wird die TCP-Verbindung geschlossen, beendet sie den Fernsteuermodus.
  → `6d-studio` hält die Session offen, beantwortet OpenSession/CloseSession von gphoto2 selbst und rechnet
  Transaktionsnummern um.
- Ohne regelmäßige Abfragen trennt die 6D nach **~30 s**. `GetDeviceInfo` reicht nicht – nötig ist Canons
  `GetEvent` (0x9116) im Fernsteuer- (0x9114) und Ereignismodus (0x9115). Studio: alle 0,5 s.
- Abschaltautomatik: Ereignis `0xC18D` (WillSoonShutdown) → mit `KeepDeviceOn` (0x911D) antworten.
  Studio schickt `KeepDeviceOn` zusätzlich jede Minute.

## Unterstützte Canon-Befehle (laut GetDeviceInfo)

| Code | Befehl | Nutzung |
|---|---|---|
| 0x910F | RemoteRelease | normales Auslösen |
| 0x9128 / 0x9129 | RemoteReleaseOn / Off | **Bulb**: On(3, 0) = Auslöser voll drücken, Off(3) = loslassen ✔︎ getestet |
| 0x9153 | GetViewFinderData(0x00100000) | Livebild, 960×640 JPEG, ~4,4 B/s über den Hotspot ✔︎ |
| 0x9158 | Zoom(1/5/10) | Lupe ✔︎ getestet (5× und zurück) |
| 0x9159 | ZoomPosition(x, y) | **offen** – Koordinatensystem angenommen 5472×3648 |
| 0x9155 | DriveLens | Fokus: 1–3 nah, 0x8001–0x8003 fern – **offen** |
| 0x911D | KeepDeviceOn | Wachhalten ✔︎ |
| – | BulbStart/BulbEnd (0x9125/0x9126) | **nicht** vorhanden |

## Eigenschaften (SetDevicePropValueEx 0x9110, Daten `[12][prop][wert]`)

| Code | Bedeutung | Werte |
|---|---|---|
| 0xD101 | Blende | f = 2^((v−8)/16), z. B. 0x38 = f/8 |
| 0xD102 | Zeit | 0x0C = Bulb, t = 2^(−(v−0x38)/8), z. B. 0x6D = 1/100 |
| 0xD103 | ISO | 0 = Auto, ISO = 100·2^((v−0x48)/8), z. B. 0x70 = 3200 |
| 0xD105 | Moduswahlrad | 3 = M, 4 = B |
| 0xD11C | Speicherort | 4 = nur Computer (Studio stellt dann auf Karte um) |
| 0xD1B0 | Livebild-Ausgabe | 2 = Computer, 0 = aus |
| 0xD1B1 | EVF-Modus | 1 = an |

Im Modus B behält 0xD102 den letzten M-Wert (z. B. 1/100) – Bulb erkennt man an 0xD105 = 4.

## Ereignisse (GetEvent-Daten: Folge von `[länge][typ][…]`, Ende = Typ 0)

| Typ | Bedeutung |
|---|---|
| 0xC189 | Eigenschaftswert: `[prop][wert]` |
| 0xC18A | erlaubte Werte: `[prop][typ][anzahl][werte…]`, je 4 Byte |
| 0xC181 | **neues Bild auf der Karte (6D)**: Handle bei Offset 8 |
| 0xC1A7 | dasselbe bei neueren Modellen (Ex64) |
| 0xC186 | Bild zum Abholen (nur bei Speicherort „Computer“) |
| 0xC18D | Kamera schaltet sich gleich ab |
| 0xC194 | Bulb-Belichtungszeit (beobachtet, nicht ausgewertet) |
| 0xC185 | Speicher-Info geändert (beobachtet) |

## Download

- `GetObjectInfo` (0x1008) → Dateiname ab Offset 52, Größe bei Offset 8; dann `GetObject` (0x1009).
- Über den iPhone-Hotspot ~**1 MB/s** → RAW (21 MB) ≈ 20 s. Währenddessen warten alle anderen Befehle.
  Idee: `GetPartialObject` (0x101B) in Stücken, damit Bedienung und Livebild weiterlaufen.
