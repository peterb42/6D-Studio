#!/bin/zsh
# Installiert 6D Studio aus diesem Projektordner.
#
# Statt Kopien werden Symlinks in ~/.local/bin angelegt – Änderungen im Projekt
# wirken dadurch sofort:  Oberfläche (HTML) nach Neuladen im Browser,
# Python-Code nach  „6d-fernsteuerung stop“ + „6d-fernsteuerung studio“.
#
#   ./install.sh            Symlinks anlegen, Abhängigkeiten prüfen
#   ./install.sh --cleanup  zusätzlich alte Kopien (~/.local/share/6d-studio) löschen
set -e
ROOT=${0:A:h}
BIN="$HOME/.local/bin"

mkdir -p "$BIN"
chmod +x "$ROOT/studio/6d-studio.py" "$ROOT/bin/6d-fernsteuerung" "$ROOT"/tools/*.py
ln -sfn "$ROOT/studio/6d-studio.py"  "$BIN/6d-studio"
ln -sfn "$ROOT/bin/6d-fernsteuerung" "$BIN/6d-fernsteuerung"
rm -f "$BIN/6d-ptpip-proxy"                       # ersetzt durch 6d-studio
echo "✔︎ 6d-studio, 6d-fernsteuerung → $ROOT"

if [[ ${1:-} == --cleanup ]]; then
  if pgrep -f "$HOME/.local/share/6d-studio" >/dev/null; then
    echo "⚠︎ Ein altes Studio läuft noch aus ~/.local/share – erst „6d-fernsteuerung stop“."
  else
    rm -rf "$HOME/.local/share/6d-studio" && echo "✔︎ alte Kopie ~/.local/share/6d-studio entfernt"
  fi
elif [[ -d $HOME/.local/share/6d-studio ]]; then
  echo "ℹ︎ Alte Kopie ~/.local/share/6d-studio vorhanden – nach dem nächsten Studio-Neustart: ./install.sh --cleanup"
fi

# Abhängigkeiten
for t in gphoto2 ffplay; do
  command -v $t >/dev/null || echo "✘ $t fehlt – brew bundle (Brewfile im Projekt)"
done
[[ ":$PATH:" == *":$BIN:"* ]] || echo "✘ $BIN ist nicht im PATH (in ~/.zshrc ergänzen)"
if [[ -x $ROOT/.venv/bin/python ]]; then
  echo "✔︎ venv: $($ROOT/.venv/bin/python --version)"
  "$ROOT/.venv/bin/python" -c "import numpy, rawpy" 2>/dev/null \
    && echo "✔︎ numpy + rawpy da – Live-Stack und Dark-/Bias-Bibliothek verfügbar" \
    || echo "ℹ︎ Für Live-Stack und Bibliothek: .venv/bin/pip install -r requirements.txt"
else
  echo "ℹ︎ Keine venv – für Live-Stack und zum Entwickeln:"
  echo "  /opt/homebrew/bin/python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
fi
grep -q '^ptp2_ip=guid=' "$HOME/.gphoto/settings" 2>/dev/null \
  && echo "✔︎ Kopplung (GUID) in ~/.gphoto/settings vorhanden" \
  || echo "ℹ︎ Noch keine Kopplung – siehe README, Abschnitt „Erstes Koppeln“"
