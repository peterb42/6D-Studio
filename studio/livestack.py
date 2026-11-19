"""Live-Stack: jedes neue Light kalibrieren, ausrichten und zum laufenden Mittel addieren.

Angelehnt an OpenLiveStacker (Ablauf, „Satelliten entfernen“, automatische Streckung nur für die Anzeige) und an
Siril (Dark-Optimierung, kosmetische Korrektur aus dem Master-Dark, Ausrichtung mit Drehung wie `-rotate`).

Je Bild (RAW, Werte in ADU):
  1. Bias abziehen – Master-Bias, sonst der Schwarzwert der Kamera (wie Sirils „=2048“).
  2. Dark: Wärmesignal = Master-Dark − Bias, mal k abziehen. Dark-Optimierung wie Siril: k ∈ [0, 4] per
     Goldener-Schnitt-Suche (Toleranz 0,001) so, dass die Streuung im 512×512-Feld der Bildmitte am kleinsten
     wird – ohne die Pixel der Hotpixel-Karte, die Schritt 3 ohnehin ersetzt. Gleicht Temperatur- und
     Zeitunterschiede zwischen Darks und Lights aus.
  3. Hotpixel: aus dem Wärmesignal bestimmt und durch gleichfarbige Nachbarn ersetzt (Siril „cosmetic
     correction“). Zusätzlich ein Filter je Bild für Reste und Treffer kosmischer Strahlung.
  4. Superpixel-Debayer (halbe Auflösung, 6D: 2748 × 1835).
  5. Sterne finden, per Dreiecks-Vergleich mit dem ersten Bild zuordnen (drehungsunabhängig), dann eine
     Homographie über alle Sterne – ein Weitwinkel bildet die Bildfelddrehung vom Stativ projektiv ab.
  6. Bilinear auf das erste Bild verzerren und aufaddieren. „Satelliten entfernen“ verwirft je Pixel den
     hellsten Wert (wie OpenLiveStacker) – Flugzeuge und Satellitenspuren verschwinden ab 3 Bildern.
  7. Anzeige: Gradient und Vignettierung ebnen (glattes Hintergrundmodell), Weißabgleich der Kamera, je
     Farbkanal automatisch gestreckt (Midtones Transfer Function wie Siril/PixInsight). Die Daten selbst
     bleiben linear (FITS).
  8. Bildanalyse (analysis.py) alle 10 Bilder und am Ende: Kalibrierung, Rauschgewinn, Hintergrund, Sterne.
JPEGs: ab Schritt 4 – ohne Kalibrierung, JPEGs sind nicht linear.
"""
import collections
import itertools
import os
import queue
import threading
import time

import astro
import imaging
from imaging import np

MIN_STARS = 6               # weniger zugeordnete Sterne → Bild verwerfen
MAX_STARS = 100
TRI_STARS = 40              # so viele helle Sterne bilden die Dreiecke
MATCH_PX = 2.0              # Toleranz der Zuordnung (Pixel im Stapel)
HOMOGRAPHY_MIN = 12         # ab so vielen Paaren projektiv ausrichten, darunter Drehen + Verschieben
OPT_CROP = 512              # Feld für die Dark-Optimierung (wie Siril)
K_MAX = 4.0                 # Dark-Faktor bis 4: Lights ~10 °C wärmer als die Darks (6D: 2,5–3,3 gemessen)
DISPLAY_W = 1600            # Breite des Anzeigebilds


# ---------------------------------------------------------------- Kalibrierung
def golden_section(f, lo, hi, tol=0.001):
    gr = (5 ** 0.5 - 1) / 2
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = f(c), f(d)
    while abs(c - d) > tol:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = f(d)
    return (a + b) / 2


def optimize_k(light, thermal, crop=OPT_CROP, hot=None):
    """Faktor k für das Wärmesignal: kleinste Streuung von (Light − k·Dark) im Feld der Bildmitte, je
    Farbkanal des Bayer-Musters gerechnet und summiert (wie Siril). hot: Pixel der Hotpixel-Karte bleiben außen
    vor – sie werden danach ohnehin ersetzt und würden k sonst nach oben treiben (echte 6D, 3 s: mit ihnen k am
    Anschlag 2, ohne sie 0,55 – bei gleichem Rauschen im Stapel)."""
    h, w = light.shape
    c = min(crop, h, w) // 2 * 2
    y0, x0 = (h - c) // 4 * 2, (w - c) // 4 * 2
    L = light[y0:y0 + c, x0:x0 + c].astype(np.float64)
    D = thermal[y0:y0 + c, x0:x0 + c].astype(np.float64)
    keep = np.ones(L.shape, bool)
    if hot is not None:
        ys, xs = hot
        inside = (ys >= y0) & (ys < y0 + c) & (xs >= x0) & (xs < x0 + c)
        keep[ys[inside] - y0, xs[inside] - x0] = False
    parts = [(L[i::2, j::2][keep[i::2, j::2]], D[i::2, j::2][keep[i::2, j::2]]) for i in (0, 1) for j in (0, 1)]
    return golden_section(lambda k: sum(float(np.std(lp - k * dp)) for lp, dp in parts), 0.0, K_MAX)


def hot_pixels(thermal, sigma=5.0, max_fraction=0.01):
    """Heiße Pixel im Wärmesignal (Median + 5σ, höchstens 1 % der Pixel) → Indizes (Zeilen, Spalten)."""
    med, noise = _robust(thermal)
    thr = med + max(sigma * noise, 1.0)
    inner = thermal[2:-2, 2:-2]
    if np.count_nonzero(inner > thr) > max_fraction * inner.size:
        thr = float(np.quantile(inner.ravel()[::7], 1 - max_fraction))
    ys, xs = np.nonzero(inner > thr)
    return ys + 2, xs + 2


def fix_pixels(cfa, hot):
    """Durch den Mittelwert der vier gleichfarbigen Nachbarn ersetzen (Abstand 2 im Bayer-Muster)."""
    ys, xs = hot
    if len(ys):
        cfa[ys, xs] = 0.25 * (cfa[ys, xs - 2] + cfa[ys, xs + 2] + cfa[ys - 2, xs] + cfa[ys + 2, xs])


def hot_filter(cfa, sigma=8.0):
    """Einzelne helle Pixel je Bild (Reste, kosmische Strahlung): deutlich heller als der Hintergrund, aber
    weder gleichfarbige noch direkte Nachbarn hell – Sterne bleiben, weil ihr Licht mehrere Pixel trifft."""
    med, noise = _robust(cfa)
    c = cfa[2:-2, 2:-2]
    n2 = np.maximum(np.maximum(cfa[2:-2, :-4], cfa[2:-2, 4:]), np.maximum(cfa[:-4, 2:-2], cfa[4:, 2:-2]))
    n1 = np.maximum(np.maximum(cfa[2:-2, 1:-3], cfa[2:-2, 3:-1]), np.maximum(cfa[1:-3, 2:-2], cfa[3:-1, 2:-2]))
    lift = c - med
    hot = (lift > sigma * max(noise, 1.0)) & (n2 - med < 0.3 * lift) & (n1 - med < 0.3 * lift)
    ys, xs = np.nonzero(hot)
    fix_pixels(cfa, (ys + 2, xs + 2))
    return len(ys)


def _robust(a):
    sample = a.ravel()[::97]
    med = float(np.median(sample))
    return med, 1.4826 * float(np.median(np.abs(sample - med)))


# ---------------------------------------------------------------- Sterne
def background(img, bs=32):
    """Grober Himmelshintergrund: Median in 32×32-Blöcken."""
    h, w = img.shape
    hb, wb = max(h // bs, 1), max(w // bs, 1)
    blocks = img[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs)[:, ::2, :, ::2]
    med = np.median(blocks.transpose(0, 2, 1, 3).reshape(hb, wb, -1), axis=2).astype(np.float32)
    bg = np.repeat(np.repeat(med, bs, 0), bs, 1)
    return np.pad(bg, ((0, h - bg.shape[0]), (0, w - bg.shape[1])), mode="edge")


def box3(a):
    p = np.pad(a, 1, mode="edge")
    return (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
            + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9


def find_stars(img, max_stars=MAX_STARS, border=8, details=False):
    """→ (Positionen (N, 2) als x, y – hellste zuerst, FWHM-Median in Pixeln oder None).
    details=True: → (Positionen, FWHM je Stern, Elongation je Stern (große/kleine Halbachse, rund = 1))."""
    r = img - background(img)
    _, noise = _robust(r)
    sm = box3(r)
    thr = 5 * max(noise, 1e-6) / 3                   # Rauschen nach 3×3-Mittel ≈ σ/3
    h, w = img.shape
    ys, xs = np.nonzero(sm[border:h - border, border:w - border] > thr)
    if not len(ys):
        return (np.zeros((0, 2)), np.zeros(0), np.zeros(0)) if details else (np.zeros((0, 2)), None)
    ys, xs = ys + border, xs + border
    v = sm[ys, xs]
    if len(v) > 50000:                               # Wolken, Vordergrund: nur die hellsten Kandidaten
        keep = np.argpartition(-v, 50000)[:50000]
        ys, xs, v = ys[keep], xs[keep], v[keep]
    peak = np.ones(len(v), bool)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            if dy or dx:
                peak &= v >= sm[ys + dy, xs + dx]
    ys, xs, v = ys[peak], xs[peak], v[peak]
    order = np.argsort(-v)[:4 * max_stars]
    ys, xs = ys[order], xs[order]
    oy, ox = np.mgrid[-3:4, -3:4]
    win = np.clip(r[ys[:, None, None] + oy, xs[:, None, None] + ox], 0, None)
    tot = win.sum((1, 2))
    ok = (tot > 0) & (r[ys, xs] < 0.6 * tot)          # fast alles Licht in einem Pixel → kein Stern
    tot = np.where(tot > 0, tot, 1)
    cx = (win * ox).sum((1, 2)) / tot
    cy = (win * oy).sum((1, 2)) / tot
    dx, dy = ox - cx[:, None, None], oy - cy[:, None, None]
    sxx, syy, sxy = ((win * dx * dx).sum((1, 2)) / tot, (win * dy * dy).sum((1, 2)) / tot,
                     (win * dx * dy).sum((1, 2)) / tot)
    root = np.sqrt((sxx - syy) ** 2 / 4 + sxy ** 2)
    elong = np.sqrt(((sxx + syy) / 2 + root) / np.maximum((sxx + syy) / 2 - root, 1e-6))
    pts = np.stack([xs + cx, ys + cy], 1)[ok]
    flux, fwhm, elong = tot[ok], 2.3548 * np.sqrt((sxx + syy)[ok] / 2), elong[ok]
    keep, kept = [], np.zeros((0, 2))
    for i in np.argsort(-flux):                      # Doppelte (Plateaus) entfernen
        if not len(kept) or np.hypot(*(kept - pts[i]).T).min() > 3:
            keep.append(i)
            kept = pts[keep]
        if len(keep) >= max_stars:
            break
    if details:
        return pts[keep], fwhm[keep], elong[keep]
    return pts[keep], (float(np.median(fwhm[keep])) if keep else None)


def frame_quality(rgb, size=512):
    """Himmelshintergrund (Median) und Rauschen (robust, ohne groben Hintergrund) im Grün der Bildmitte."""
    h, w = rgb.shape[:2]
    c = min(size, h, w)
    g = rgb[(h - c) // 2:(h + c) // 2, (w - c) // 2:(w + c) // 2, 1]
    return float(np.median(g[::3, ::3])), _robust(g - background(g))[1]


# ---------------------------------------------------------------- Ausrichtung
def triangles(pts, k=5):
    """Dreiecke aus jedem Stern und je zwei seiner 5 nächsten Nachbarn. Ecken sortiert nach der Länge der
    gegenüberliegenden Seite; Invarianten = log der Seitenverhältnisse (unabhängig von Lage, Drehung, Maßstab)."""
    n = len(pts)
    d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    nn = np.argsort(d, 1)[:, 1:k + 1]
    tris = np.array(sorted({tuple(sorted((i, int(a), int(b))))
                            for i in range(n) for a, b in itertools.combinations(nn[i], 2)}))
    p = pts[tris]
    opp = np.stack([np.hypot(*(p[:, 1] - p[:, 2]).T), np.hypot(*(p[:, 0] - p[:, 2]).T),
                    np.hypot(*(p[:, 0] - p[:, 1]).T)], 1)
    order = np.argsort(opp, 1)
    tris, sides = np.take_along_axis(tris, order, 1), np.take_along_axis(opp, order, 1)
    ok = sides[:, 0] > 3
    inv = np.log(np.stack([sides[:, 2] / np.maximum(sides[:, 1], 1e-9),
                           sides[:, 1] / np.maximum(sides[:, 0], 1e-9)], 1))
    return tris[ok], inv[ok]


def fit_similarity(zs, zd):
    """Kleinste Quadrate für z' = a·z + t (a komplex: Drehung und Maßstab)."""
    ms, md = zs.mean(), zd.mean()
    s = zs - ms
    a = np.vdot(s, zd - md) / max(np.vdot(s, s).real, 1e-12)
    return a, md - a * ms


def similarity_matrix(a, t):
    """z' = a·z + t als 3×3-Matrix (homogene Koordinaten)."""
    return np.array([[a.real, -a.imag, t.real], [a.imag, a.real, t.imag], [0.0, 0.0, 1.0]])


def fit_homography(src, dst):
    """Projektive Transformation src → dst nach kleinsten Quadraten (DLT mit Normierung nach Hartley).
    Ein Weitwinkel bildet die Drehung des Himmels um die Kamera exakt so ab (Bild = K·R·K⁻¹)."""
    def norm(p):
        m = p.mean(0)
        s = 2 ** 0.5 / max(float(np.mean(np.hypot(*(p - m).T))), 1e-9)
        return np.array([[s, 0, -s * m[0]], [0, s, -s * m[1]], [0, 0, 1.0]])
    ts, td = norm(src), norm(dst)
    ps, pd = src * ts[0, 0] + ts[:2, 2], dst * td[0, 0] + td[:2, 2]
    x, y, u, v = ps[:, 0], ps[:, 1], pd[:, 0], pd[:, 1]
    z, o = np.zeros_like(x), np.ones_like(x)
    a = np.concatenate([np.stack([-x, -y, -o, z, z, z, u * x, u * y, u], 1),
                        np.stack([z, z, z, -x, -y, -o, v * x, v * y, v], 1)])
    h = np.linalg.svd(a)[2][-1].reshape(3, 3)
    h = np.linalg.inv(td) @ h @ ts
    return h / h[2, 2]


def project(H, pts):
    """Punkte (N, 2) mit der 3×3-Matrix H abbilden."""
    q = pts @ H[:2, :2].T + H[:2, 2]
    return q / (pts @ H[2, :2] + H[2, 2])[:, None]


def _pairs(H, src, dst, tol):
    d = np.hypot(*(project(H, src)[:, None, :] - dst[None, :, :]).transpose(2, 0, 1))
    j = d.argmin(1)
    ok = d[np.arange(len(src)), j] < tol
    return np.nonzero(ok)[0], j[ok]


def describe(H, center):
    """Verschiebung (dx, dy) der Bildmitte und Drehung in Grad – zum Anzeigen."""
    c = np.asarray(center, float)
    d = project(H, c[None])[0] - c
    return float(d[0]), float(d[1]), float(np.degrees(np.arctan2(H[1, 0] - H[0, 1], H[0, 0] + H[1, 1])))


def register(src, dst, tol=MATCH_PX):
    """Transformation src → dst (Sternpositionen (N, 2)). → (H 3×3, Anzahl Paare, Restfehler in px) oder None.

    Erst eine Ähnlichkeit (Drehen, Verschieben) aus passenden Dreiecken – robust und drehungsunabhängig; dann
    schrittweise eine Homographie über alle Sterne. Gemessen an der 6D mit 24 mm nach 0,7° Bildfelddrehung:
    Drehen + Verschieben lässt Sterne am Rand 7 px daneben (Doppelsterne), die Homographie 0,2 px."""
    if len(src) < MIN_STARS or len(dst) < MIN_STARS:
        return None
    zs, zd = src[:, 0] + 1j * src[:, 1], dst[:, 0] + 1j * dst[:, 1]
    ts, inv_s = triangles(src[:TRI_STARS])
    td, inv_d = triangles(dst[:TRI_STARS])
    if not len(ts) or not len(td):
        return None
    d2 = ((inv_s[:, None, :] - inv_d[None, :, :]) ** 2).sum(-1)
    near = d2.argmin(1)
    score = d2[np.arange(len(ts)), near]
    best, best_n = None, 0
    for i in np.argsort(score)[:200]:
        if score[i] > 0.03 ** 2:
            break
        a, t = fit_similarity(zs[ts[i]], zd[td[near[i]]])
        if abs(abs(a) - 1) > 0.03:                   # gleiche Optik: Maßstab ~1
            continue
        H = similarity_matrix(a, t)
        n = len(_pairs(H, src, dst, 3 * tol)[0])     # großzügig: am Rand weicht die Ähnlichkeit ab
        if n > best_n:
            best, best_n = H, n
            if n >= 0.8 * min(len(zs), len(zd)):
                break
    if best is None or best_n < MIN_STARS:
        return None
    H = best
    for f in (8, 4, 2, 1, 1):                         # Toleranz schrittweise enger, Randsterne kommen hinzu
        i, j = _pairs(H, src, dst, f * tol)
        if len(i) < MIN_STARS:
            return None
        H = (fit_homography(src[i], dst[j]) if len(i) >= HOMOGRAPHY_MIN
             else similarity_matrix(*fit_similarity(zs[i], zd[j])))
    i, j = _pairs(H, src, dst, tol)
    c = dst.mean(0)                                   # Jacobi-Matrix in der Mitte: Maßstab muss ~1 bleiben
    jac = (H[:2, :2] - np.outer(project(H, c[None])[0], H[2, :2])) / (H[2, :2] @ c + H[2, 2])
    scale = np.linalg.svd(jac, compute_uv=False)
    if len(i) < MIN_STARS or abs(H[2, :2]).sum() * float(np.ptp(dst, 0).max()) > 0.1 or abs(scale - 1).max() > 0.05:
        return None                                   # unplausibel: gleiche Optik, kaum Perspektive
    rms = float(np.sqrt(np.mean(np.sum((project(H, src[i]) - dst[j]) ** 2, 1))))
    return H, len(i), rms


def warp(img, H, rows=256):
    """Bild (H, W, 3) so verzerren, dass es auf dem Referenzbild liegt (Referenz = H·Bild). → (Bild, gültig)."""
    h, w, _ = img.shape
    out = np.zeros_like(img)
    valid = np.zeros((h, w), bool)
    b = np.linalg.inv(H)                              # je Zielpixel: woher im Bild?
    zx = np.arange(w, dtype=np.float64)[None, :]
    for y0 in range(0, h, rows):
        zy = np.arange(y0, min(h, y0 + rows), dtype=np.float64)[:, None]
        q = b[2, 0] * zx + b[2, 1] * zy + b[2, 2]
        sx = ((b[0, 0] * zx + b[0, 1] * zy + b[0, 2]) / q).astype(np.float32)
        sy = ((b[1, 0] * zx + b[1, 1] * zy + b[1, 2]) / q).astype(np.float32)
        x0, y0f = np.floor(sx), np.floor(sy)
        fx, fy = (sx - x0)[..., None], (sy - y0f)[..., None]
        xi, yi = x0.astype(np.int32), y0f.astype(np.int32)
        ok = (xi >= 0) & (xi < w - 1) & (yi >= 0) & (yi < h - 1)
        xi, yi = np.clip(xi, 0, w - 2), np.clip(yi, 0, h - 2)
        v = ((img[yi, xi] * (1 - fx) + img[yi, xi + 1] * fx) * (1 - fy)
             + (img[yi + 1, xi] * (1 - fx) + img[yi + 1, xi + 1] * fx) * fy)
        v[~ok] = 0
        out[y0:y0 + len(zy)] = v
        valid[y0:y0 + len(zy)] = ok
    return out, valid


# ---------------------------------------------------------------- Anzeige
def mtf(m, x):
    """Midtones Transfer Function: 0 → 0, m → 0,5, 1 → 1 (Nenner ist für 0 < m < 1 nie null)."""
    x = np.clip(x, 0, 1)
    return (m - 1) * x / ((2 * m - 1) * x - m)


def stretch(img, covered, wb, white, target=0.25, shadows=-2.8):
    """Automatische Streckung je Farbkanal (unverknüpft → Hintergrund neutral grau). → uint8 (H, W, 3)."""
    out = np.zeros(img.shape, np.uint8)
    if not covered.any():
        return out
    for c in range(3):
        ch = img[..., c] * (wb[c] / white)
        vals = ch[covered][::7]
        med = float(np.median(vals))
        madn = 1.4826 * float(np.median(np.abs(vals - med)))
        c0 = med + shadows * madn
        span = max(1.0 - c0, 1e-6)
        x = np.clip((ch - c0) / span, 0, 1)
        mx = min(max((med - c0) / span, 1e-6), 1 - 1e-6)
        m = float(mtf(target, np.float64(mx)))       # Mitte so wählen, dass der Hintergrund auf target landet
        out[..., c] = (mtf(m, x) * 255 + 0.5).astype(np.uint8)
    out[~covered] = 0
    return out


def _sky_terms(x, y):
    r2 = x * x + y * y
    return [np.ones_like(x), x, y, r2, r2 * r2]


def sky_model(img, covered, rounds=4):
    """Glatter Himmelshintergrund je Farbkanal: Ebene (Gradient, z. B. Horizont) + radiale Vignettierung,
    c0 + c1·x + c2·y + c3·r² + c4·r⁴ (x, y auf die halbe Bilddiagonale normiert: r = 1 in den Ecken).
    Angepasst an Block-Mediane; helle Blöcke (Milchstraße, Lampen, Sternhaufen) fallen schrittweise heraus –
    deshalb bleibt die Milchstraße erhalten, anders als bei einem Polynom hohen Grades.
    → (Modell wie img, Koeffizienten (3, 5)) oder (None, None)."""
    h, w = img.shape[:2]
    bs = max(8, w // 48)
    hb, wb = h // bs, w // bs
    if hb < 4 or wb < 4:
        return None, None
    blocks = img[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs, 3)[:, ::2, :, ::2]
    med = np.median(blocks.transpose(0, 2, 1, 3, 4).reshape(hb, wb, -1, 3), axis=2).reshape(-1, 3)
    full = covered[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs).all((1, 3)).ravel()
    if full.sum() < 20:
        return None, None
    half = np.hypot(w, h) / 2
    yc, xc = np.meshgrid((np.arange(hb) + 0.5) * bs, (np.arange(wb) + 0.5) * bs, indexing="ij")
    a = np.stack(_sky_terms(((xc - w / 2) / half).ravel(), ((yc - h / 2) / half).ravel()), 1)
    coef = np.zeros((3, 5))
    for c in range(3):
        v, ok = med[:, c], full.copy()
        for _ in range(rounds):
            coef[c] = np.linalg.lstsq(a[ok], v[ok], rcond=None)[0]
            r = v - a @ coef[c]
            s = 1.4826 * float(np.median(np.abs(r[ok]))) + 1e-9
            ok = full & (r < 2.5 * s) & (r > -4 * s)
            if ok.sum() < 20:
                break
    x = ((np.arange(w, dtype=np.float32) - w / 2) / half)[None, :]
    y = ((np.arange(h, dtype=np.float32) - h / 2) / half)[:, None]
    terms = _sky_terms(x + 0 * y, y + 0 * x)
    model = np.stack([sum(np.float32(coef[c, i]) * terms[i] for i in range(5)) for c in range(3)], 2)
    return model, coef


def flatten(img, covered):
    """Für die Anzeige: Gradient und Vignettierung abziehen, Hintergrund überall wie in der Bildmitte."""
    model, coef = sky_model(img, covered)
    if model is None:
        return img
    return img - model + coef[:, 0].astype(np.float32)


def shrink(img, covered, width=DISPLAY_W):
    f = -(-img.shape[1] // width)
    if f <= 1:
        return img, covered
    h, w = img.shape[0] // f * f, img.shape[1] // f * f
    small = img[:h, :w].reshape(h // f, f, w // f, f, 3).mean((1, 3))
    cov = covered[:h, :w].reshape(h // f, f, w // f, f).all((1, 3))
    return small, cov


# ---------------------------------------------------------------- Stapel
class LiveStack:
    """Nimmt Bilddateien an (add), rechnet sie in einem eigenen Thread ein und hält das Anzeigebild bereit."""

    OPTIONS = {"bias": "auto", "dark": "auto", "optimize": True, "hotpixels": True, "satellites": True,
               "stretch": 0.25, "black": -2.8, "flatten": True}
    ANALYZE_EVERY = 10          # Bildanalyse nach so vielen Bildern (und am Ende)

    def __init__(self, library, out_dir, options=None, log=print, name="Live-Stack"):
        problem = imaging.missing()
        if problem:
            raise RuntimeError(problem)
        self.library, self.out_dir, self.log, self.name = library, out_dir, log, name
        self.opts = dict(self.OPTIONS)
        self.opts.update({k: v for k, v in (options or {}).items() if k in self.OPTIONS and v is not None})
        self.lock = threading.RLock()
        self.queue = queue.Queue()
        self.active, self.done = True, False
        self.busy = None
        self.calib, self.calib_info = None, {}
        self.png, self.seq = None, 0
        self.saved = []
        self.analysis_lock = threading.Lock()
        self._clear()
        threading.Thread(target=self._run, daemon=True).start()

    def _clear(self):
        self.kind = self.shape = self.ref = None
        self.sum = self.count = self.maxv = None
        self.frames = self.rejected = 0
        self.exposure = 0.0
        self.history = collections.deque(maxlen=30)
        self.stats, self.rejects = [], []                 # alle Bilder – für die Analyse
        self.analysis = None
        self.wb, self.white = (1.0, 1.0, 1.0), 1.0
        self.first_meta = {}

    # ---------- von außen
    def add(self, path, meta=None):
        if self.active:
            self.queue.put((path, meta or {}))

    def finish(self):
        """Keine neuen Bilder mehr; die Warteschlange wird abgearbeitet, dann gespeichert."""
        if self.active:
            self.active = False
            self.queue.put(None)

    def reset(self):
        with self.lock:
            self._clear()
            self.png = None
            self.seq += 1
        self.log("Live-Stack zurückgesetzt – das nächste Bild ist die neue Referenz")

    def set_view(self, stretch_target=None, black=None, flatten=None):
        with self.lock:
            if stretch_target is not None:
                self.opts["stretch"] = min(0.6, max(0.02, float(stretch_target)))
            if black is not None:
                self.opts["black"] = min(0.0, max(-10.0, float(black)))
            if flatten is not None:
                self.opts["flatten"] = bool(flatten)
        self._render()

    def request_analysis(self):
        """Bildanalyse im Hintergrund (auch nach dem Ende des Stapels)."""
        if self.frames:
            threading.Thread(target=self._analyze, daemon=True).start()

    def status(self):
        with self.lock:
            return {"active": self.active, "done": self.done, "frames": self.frames, "rejected": self.rejected,
                    "exposure": round(self.exposure, 1), "kind": self.kind, "queue": self.queue.qsize(),
                    "busy": self.busy, "seq": self.seq, "history": list(self.history)[:12],
                    "calib": self.calib_info, "opts": self.opts, "temp": self.first_meta.get("temp"),
                    "iso": self.first_meta.get("iso"),
                    "out": self.out_dir.replace(os.path.expanduser("~"), "~"),
                    "saved": [os.path.basename(p) for p in self.saved],
                    "size": [self.shape[1], self.shape[0]] if self.shape else None,
                    "analysis": self.analysis, "analyzing": self.analysis_lock.locked()}

    # ---------- Verarbeitung
    def _run(self):
        while True:
            item = self.queue.get()
            if item is None:
                if self.frames:
                    try:
                        self.save()
                    except Exception as e:
                        self.log("❌ Live-Stack speichern:", repr(e))
                    self._analyze()
                with self.lock:
                    self.done = True
                return
            path, meta = item
            self.busy = os.path.basename(path)
            t0 = time.time()
            try:
                info = self._process(path, meta)
            except Exception as e:
                info = {"ok": False, "reason": "Fehler: %s" % e}
                self.log("❌ Live-Stack %s: %r" % (os.path.basename(path), e))
            self.busy = None
            if info is None:                         # z. B. JPEG neben dem RAW – still übergehen
                continue
            info.update(name=os.path.basename(path), s=round(time.time() - t0, 1))
            with self.lock:
                self.history.appendleft(info)
                if not info["ok"]:
                    self.rejected += 1
                    self.rejects.append((info["name"], info["reason"]))
            if info["ok"]:
                self.log("✚ Live-Stack %d: %s (%d Sterne, %.1f s)" % (self.frames, info["name"], info["matched"],
                                                                     info["s"]))
                if self.frames % self.ANALYZE_EVERY == 0:
                    self._analyze()
            else:
                self.log("✖︎ Live-Stack verwirft %s: %s" % (info["name"], info["reason"]))

    def _process(self, path, meta):
        kind = imaging.kind_of(path)
        if kind not in ("raw", "jpeg") or (self.kind and kind != self.kind):
            return None
        frame = imaging.load(path)
        meta = dict(frame.meta, **{k: v for k, v in meta.items() if v is not None})
        info = {"ok": False, "temp": meta.get("temp")}
        if frame.cfa:
            self._calibrate(frame, meta, info)
            rgb = imaging.superpixel(frame.data)
        else:
            rgb = frame.data
        del frame.data
        stars, fwhm = find_stars(rgb.sum(axis=2))
        info.update(stars=len(stars), fwhm=round(fwhm, 2) if fwhm else None)
        with self.lock:
            ref, shape = self.ref, self.shape
        if ref is None:
            if len(stars) < MIN_STARS:
                info["reason"] = "nur %d Sterne – Referenz braucht mindestens %d" % (len(stars), MIN_STARS)
                return info
            H, matched, rms = np.eye(3), len(stars), 0.0
            warped, valid = rgb, np.ones(rgb.shape[:2], bool)
        else:
            if rgb.shape != shape:
                info["reason"] = "andere Bildgröße als das erste Bild"
                return info
            reg = register(stars, ref)
            if reg is None:
                info["reason"] = "Ausrichtung fehlgeschlagen (%d Sterne gefunden)" % len(stars)
                return info
            H, matched, rms = reg
            warped, valid = warp(rgb, H)
        del rgb
        bg, noise = frame_quality(warped)                 # nach dem Verzerren – wie es in den Stapel eingeht
        with self.lock:
            if self.ref is None:
                self.ref, self.shape, self.kind = stars, warped.shape, kind
                self.sum = np.zeros(warped.shape, np.float32)
                self.maxv = np.full(warped.shape, -np.inf, np.float32)
                self.count = np.zeros(warped.shape[:2], np.uint16)
                self.wb, self.white = frame.wb, max(frame.white - frame.black, 1.0)
                self.first_meta = meta
            self.sum += warped
            self.count += valid
            np.maximum(self.maxv, np.where(valid[..., None], warped, -np.inf), out=self.maxv)
            self.frames += 1
            self.exposure += float(meta.get("exposure") or 0)
            dx, dy, rot = describe(H, (warped.shape[1] / 2, warped.shape[0] / 2))
            info.update(ok=True, matched=matched, dx=round(dx, 1), dy=round(dy, 1), rot=round(rot, 3),
                        rms=round(rms, 2), bg=round(bg, 1), noise=round(noise, 2), exposure=meta.get("exposure"))
            self.stats.append(info)
        self._render()
        return info

    def _calibrate(self, frame, meta, info):
        if self.calib is None:
            self._load_calib(frame, meta)
        c, x = self.calib, frame.data
        x -= c["bias"] if c["bias"] is not None else frame.black
        if c["thermal"] is not None:
            k = optimize_k(x, c["thermal"], hot=c["hot"]) if self.opts["optimize"] else c["k_exp"]
            x -= np.float32(k) * c["thermal"]
            fix_pixels(x, c["hot"])
            info["k"] = round(float(k), 3)
        if self.opts["hotpixels"]:
            info["hot"] = hot_filter(x)

    def _pick(self, kind, frame, meta):
        choice = self.opts[kind]
        if choice in ("none", "", None):
            return None
        if choice == "auto":
            return self.library.best(kind, meta.get("iso"), meta.get("exposure"), meta.get("temp"),
                                     frame.data.shape, meta.get("model"), optimize=self.opts["optimize"])
        e = self.library.get(choice)
        if e and (e["h"], e["w"]) != frame.data.shape:
            self.log("⚠️ %s passt nicht zur Bildgröße – wird nicht verwendet" % e["name"])
            return None
        return e

    def _load_calib(self, frame, meta):
        bias_e, dark_e = self._pick("bias", frame, meta), self._pick("dark", frame, meta)
        bias = imaging.read_fits(self.library.path(bias_e["id"]))[0] if bias_e else None
        thermal = hot = None
        k_exp, thermal_level, dark_noise = 1.0, None, None
        if dark_e:
            thermal = imaging.read_fits(self.library.path(dark_e["id"]))[0]
            thermal -= bias if bias is not None else frame.black
            hot = hot_pixels(thermal)
            if dark_e["exposure"] and meta.get("exposure"):
                k_exp = meta["exposure"] / dark_e["exposure"]
            thermal_level = float(np.median(thermal[::7, ::7]))      # Wärmesignal je Pixel (ADU)
            green = (thermal[0::2, 1::2] + thermal[1::2, 0::2]) / 2   # wie im Stapel (Superpixel)
            dark_noise = _robust(green)[1]
        self.calib = {"bias": bias, "thermal": thermal, "hot": hot, "k_exp": k_exp}
        self.calib_info = {
            "bias": bias_e["name"] if bias_e else None, "dark": dark_e["name"] if dark_e else None,
            "dark_temp": dark_e["temp"] if dark_e else None, "dark_exposure": dark_e["exposure"] if dark_e else None,
            "hot": int(len(hot[0])) if hot is not None else 0,
            "offset": None if bias is not None else frame.black,
            "bias_n": bias_e["n"] if bias_e else None, "bias_iso": bias_e["iso"] if bias_e else None,
            "dark_n": dark_e["n"] if dark_e else None,
            "thermal": round(thermal_level, 3) if thermal_level is not None else None,
            "dark_noise": round(dark_noise, 3) if dark_noise is not None else None,
        }
        self.log("Live-Stack Kalibrierung: Bias %s · Dark %s%s" % (
            bias_e["name"] if bias_e else "– (Schwarzwert %d)" % frame.black,
            dark_e["name"] if dark_e else "–",
            " · %d Hotpixel" % len(hot[0]) if hot is not None else ""))

    # ---------- Ergebnis
    def mean_image(self):
        with self.lock:
            if self.sum is None:
                return None, None
            cnt = self.count.astype(np.float32)[..., None]
            if self.opts["satellites"] and self.frames >= 3:
                img = np.where(cnt >= 3, (self.sum - np.where(cnt > 0, self.maxv, 0)) / np.maximum(cnt - 1, 1),
                               self.sum / np.maximum(cnt, 1))
            else:
                img = self.sum / np.maximum(cnt, 1)
            return img.astype(np.float32), self.count > 0

    def display(self, img, covered):
        """Anzeigebild (uint8): auf Wunsch Gradient und Vignettierung geebnet, dann automatisch gestreckt."""
        if self.opts["flatten"]:
            img = flatten(img, covered)
        return stretch(img, covered, self.wb, self.white, self.opts["stretch"], self.opts["black"])

    def _render(self):
        img, covered = self.mean_image()
        if img is None:
            return
        small, cov = shrink(img, covered)
        png = imaging.png_bytes(self.display(small, cov))
        with self.lock:
            self.png = png
            self.seq += 1

    def snapshot(self):
        """Alles, was die Bildanalyse braucht (Kopien)."""
        img, covered = self.mean_image()
        with self.lock:
            stats = [dict(s) for s in self.stats]
            exps = sorted(s["exposure"] for s in stats if s.get("exposure"))
            snap = {"img": img, "covered": covered, "frames": self.frames, "kind": self.kind, "white": self.white,
                    "stats": stats, "rejected": list(self.rejects), "calib": dict(self.calib_info),
                    "iso": self.first_meta.get("iso"), "exposure": exps[len(exps) // 2] if exps else None}
        if img is not None:
            snap["stack_noise"] = frame_quality(img)[1]
        return snap

    def _analyze(self):
        import analysis                                 # hier: analysis braucht dieses Modul
        if not self.analysis_lock.acquire(blocking=False):
            return
        try:
            snap = self.snapshot()
            if snap["img"] is None:
                return
            result = analysis.analyze(snap)
            with self.lock:
                self.analysis = result
                self.seq += 1
        except Exception as e:
            self.log("❌ Bildanalyse: %r" % e)
        finally:
            self.analysis_lock.release()

    def save(self):
        img, covered = self.mean_image()
        if img is None:
            raise RuntimeError("Noch nichts gestapelt")
        os.makedirs(self.out_dir, exist_ok=True)
        m = self.first_meta
        base = os.path.join(self.out_dir, "LIVESTACK_%s_%dx%s_%s" % (
            time.strftime("%Y%m%d-%H%M%S"), self.frames,
            astro.exposure_tag(self.exposure / self.frames if self.frames else None), self.kind or ""))
        cards = [("IMAGETYP", "Live Stack", ""), ("INSTRUME", m.get("model") or "", ""),
                 ("ISOSPEED", m.get("iso"), ""), ("EXPTIME", round(self.exposure, 2), "Summe [s]"),
                 ("NCOMBINE", self.frames, ""), ("CCD-TEMP", m.get("temp"), "[C] erstes Bild"),
                 ("BIASUSED", self.calib_info.get("bias") or "", ""),
                 ("DARKUSED", self.calib_info.get("dark") or "", ""), ("CREATOR", "6D Studio", "")]
        if self.kind == "raw":
            imaging.write_fits(base + ".fits", img, cards)
        with open(base + ".png", "wb") as f:
            f.write(imaging.png_bytes(self.display(img, covered)))
        self.saved.append(base + ".png")
        self.log("💾 Live-Stack gespeichert: %s" % base.replace(os.path.expanduser("~"), "~"))
        return base
