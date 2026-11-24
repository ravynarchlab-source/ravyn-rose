#!/usr/bin/env bash
# Pen mode: an optional module of ravyn-rose for touch-only (capacitive) styluses.
#
#   pen/install.sh --dry-run    print every step, change nothing
#   pen/install.sh              install the DRAW / NAVIGATE switch and set up Rnote, Xournal++ and Krita
#   --no-deps                   do not install packages (only report what is missing)
#
# `./install.sh --pen` in the repository root runs this after the rice; on its own it installs
# only the pen module and does not touch the rice.
# Before any app setting is changed, the value found is saved to ~/.local/state/pen-mode, and
# `pen-mode uninstall` (or ./uninstall.sh) puts those values back.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY=0; DEPS=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --no-deps) DEPS=0 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

SHARE="$HOME/.local/share/pen-mode"
BIN="$HOME/.local/bin"
UNITS="$HOME/.config/systemd/user"

step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then printf '   would run:'; printf ' %q' "$@"; printf '\n'; else "$@"; fi
}

step "Pen mode: checking this machine"
[ "$(id -u)" -ne 0 ] || { echo "Run this as your normal user, not as root." >&2; exit 1; }
command -v pacman >/dev/null || { echo "pacman not found: this installer is for Arch-based systems." >&2; exit 1; }
apps=()
for a in rnote xournalpp krita; do
  if pacman -Qq "$a" >/dev/null 2>&1; then apps+=("$a"); else note "$a is not installed: it is skipped"; fi
done
[ ${#apps[@]} -gt 0 ] || note "none of Rnote, Xournal++, Krita is installed; the switch will have nothing to switch"
for p in rnote xournalpp krita; do
  if pgrep -x "$p" >/dev/null 2>&1; then
    echo "$p is running. Close Rnote, Xournal++ and Krita first: they write their settings back when they exit." >&2
    [ "$DRY" = 1 ] || exit 1
  fi
done

step "Pen mode: packages"
need=(python pyside6 qt6-declarative layer-shell-qt glib2 kconfig qt6-tools)   # tray icon and pill, gdbus/gsettings, kwriteconfig6
case " ${apps[*]} " in *" krita "*) need+=(python-pyqt6) ;; esac      # Krita's Python plugins
missing=()
for p in "${need[@]}"; do pacman -Qq "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ ${#missing[@]} -eq 0 ]; then
  note "everything is already installed"
elif [ "$DEPS" = 0 ]; then
  note "missing: ${missing[*]}"
  note "--no-deps given: not installing them"
else
  note "installing: ${missing[*]}"
  run sudo pacman -S --needed "${missing[@]}"
fi

step "Pen mode: files"
run mkdir -p "$SHARE/krita" "$BIN" "$UNITS"
run install -m755 "$HERE/pen-mode" "$HERE/pen-mode-tray.py" "$HERE/pen-probe.py" "$SHARE/"
run install -m644 "$HERE/pen-mode-pill.qml" "$SHARE/"
run cp -a "$HERE/krita/." "$SHARE/krita/"
run ln -sfn "$SHARE/pen-mode" "$BIN/pen-mode"
run install -m644 "$HERE/pen-mode-tray.service" "$UNITS/pen-mode-tray.service"

step "Pen mode: app settings, shortcut, tray icon"
run "$SHARE/pen-mode" setup

step "Pen mode: done"
if [ "$DRY" = 1 ]; then
  note "dry run: nothing was changed"
else
  note "tap the tray icon or press Meta+Alt+D to switch between DRAW and NAVIGATE"
  note "pen-mode status      what each app is set to"
  note "pen-mode uninstall   put the apps' own settings back and remove the module"
  note "not sure what kind of pen you have?  python3 ~/.local/share/pen-mode/pen-probe.py ~/pen-probe"
fi
