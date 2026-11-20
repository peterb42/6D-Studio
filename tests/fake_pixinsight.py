"""Ersatz für PixInsight in den Tests: eine Installation wie PixInsight 1.9.6 (macOS-Aufbau) mit einem Programm,
das die Ereigniszeilen der Skripte aus studio/pixinsight/ ausgibt und Dateien wie WBPP 3.1 hinterlässt.

    WBPP.js         zählt die Lights in dir=/Light, schreibt master/masterLight_…_autocrop.xisf (EXPTIME 2 s) und
                    5000 Bytes Zwischenbild in calibrated/, meldet einen fehlgeschlagenen Schritt (Astrometrie)
    6d-fertig.js    schreibt <Ziel>.jpg und .tif; enthält die Eingabe „kaputt“, meldet es einen Fehler
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GROUP = "Light frames, 5472x3648, NoFilter, CFA, 1x1, 2.00s, frames = %d (%d active)"

SCRIPT = """\
import glob, os, sys
sys.path.insert(0, %(here)r)
import numpy as np, xisf_helper
run = next(a for a in sys.argv if a.startswith('-r='))[3:].split(',')
say = lambda s: print('6D>> ' + s, flush=True)
print('PixInsight Core 1.9.6 Lockhart (arm64)')
if run[0].endswith('WBPP.js'):
    opt = dict(a.split('=', 1) for a in run[1:] if '=' in a)
    n = len(glob.glob(os.path.join(opt['dir'], 'Light', '*')))
    out, g = opt['outputDirectory'], %(group)r %% (n, n)
    say('pipeline 4')
    say('start 1/4 Calibration | ' + g)
    os.makedirs(os.path.join(out, 'calibrated'), exist_ok=True)
    open(os.path.join(out, 'calibrated', 'a_c.xisf'), 'wb').write(b'x' * 5000)
    say('done 1/4 Calibration | %%s | 2 | %%d calibrated' %% (g, n))
    say('start 3/4 Integration | ' + g)
    os.makedirs(os.path.join(out, 'master'), exist_ok=True)
    xisf_helper.write_xisf(os.path.join(out, 'master', 'masterLight_BIN-1_RGB_autocrop.xisf'),
                           np.full((8, 12, 3), 0.1, np.float32), [('EXPTIME', '2.000')])
    say('done 3/4 Integration | %%s | 2 | %%d integrated' %% (g, n))
    say('done 4/4 Astrometric solution | %%s | 3 | 1 failed' %% g)
    say('end')
else:
    src, target = run[1], run[2]
    if 'kaputt' in src:
        say('error Bild kaputt')
        sys.exit(0)
    for s in ('Bild laden', 'Strecken', 'Speichern'):
        say('step ' + s)
    for ext in ('.jpg', '.tif'):
        open(target + ext, 'wb').write(b'x')
        say('saved ' + target + ext)
    say('fertig')
"""


def make(root):
    """→ Pfad des Programms (…/PixInsight/PixInsight.app/Contents/MacOS/PixInsight), daneben src/…/WBPP.js."""
    exe = os.path.join(root, "PixInsight", "PixInsight.app", "Contents", "MacOS", "PixInsight")
    wbpp = os.path.join(root, "PixInsight", "src", "scripts", "BatchPreprocessing", "WBPP.js")
    for p in (exe, wbpp):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    open(wbpp, "w").close()
    with open(exe, "w") as f:
        f.write("#!%s\n" % sys.executable + SCRIPT % {"here": HERE, "group": GROUP})
    os.chmod(exe, 0o755)
    return exe
