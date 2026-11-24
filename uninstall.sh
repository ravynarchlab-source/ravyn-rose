#!/usr/bin/env bash
# ravyn-rose uninstaller.
#
#   ./uninstall.sh --dry-run   print every step, change nothing
#   ./uninstall.sh             turn the rice off, remove it, put back what install.sh replaced
#   --purge                    also delete the backup folder afterwards
#
# Order: 1. `rice off` restores your colours, wallpaper and window rules exactly as they were saved.
#        2. Every rice file, launcher and shortcut is removed.
#        3. Files that existed before install.sh ran are copied back from the backup.
# Packages are left installed.
set -uo pipefail

DRY=0; PURGE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

INSTALLED="$HOME/.local/share/rice"
BIN="$HOME/.local/bin"
UNITS="$HOME/.config/systemd/user"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/ravyn-rose"
BACKUP="$STATE/backup"
MANIFEST="$STATE/manifest.tsv"

step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then printf '   would run:'; printf ' %q' "$@"; printf '\n'; else "$@"; fi
}

step "Rice"
if [ -x "$INSTALLED/rice" ]; then
  note "rice uninstall: turns it off, restores the saved look, removes files, launchers and shortcuts"
  run "$INSTALLED/rice" uninstall
else
  note "no installed copy at $INSTALLED"
fi

step "Tablet-mode panel service"
if [ -e "$UNITS/touch-mode-panel.service" ] || [ -e "$BIN/touch-mode-panel" ]; then
  run systemctl --user disable --now touch-mode-panel.service     # stopping it puts the panel back to normal size
  [ -x "$BIN/touch-mode-panel" ] && run "$BIN/touch-mode-panel" off
  run rm -f "$BIN/touch-mode-panel" "$UNITS/touch-mode-panel.service"
  run rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/touch-mode-panel"
  run systemctl --user daemon-reload
else
  note "not installed"
fi

step "Pen mode"
if [ -x "$HOME/.local/share/pen-mode/pen-mode" ]; then
  note "pen-mode uninstall: puts the apps' own settings back, removes the switch, its shortcut and tray icon"
  if [ "$PURGE" = 1 ]; then run "$HOME/.local/share/pen-mode/pen-mode" uninstall --purge
  else run "$HOME/.local/share/pen-mode/pen-mode" uninstall; fi
else
  note "not installed"
fi

step "Putting back what was there before the install"
if [ -f "$MANIFEST" ]; then
  restored=0
  while IFS=$'\t' read -r state rel; do
    [ "$state" = existed ] || continue
    if [ -e "$BACKUP/$rel" ] || [ -L "$BACKUP/$rel" ]; then
      note "restoring ~/$rel"
      run rm -rf "${HOME:?}/${rel:?}"
      run mkdir -p "$(dirname "$HOME/$rel")"
      run cp -a "$BACKUP/$rel" "$HOME/$rel"
      restored=$((restored + 1))
    else
      note "WARNING: ~/$rel was backed up but the copy is gone from $BACKUP"
    fi
  done < "$MANIFEST"
  [ "$restored" -eq 0 ] && note "nothing to put back: none of those files existed before"
  if [ "$PURGE" = 1 ]; then
    run rm -rf "$STATE"
  else
    note "backup and reference copies kept in $STATE (delete it when you are sure, or use --purge)"
  fi
else
  note "no manifest at $MANIFEST: install.sh never ran here, or the backup was already removed"
fi

step "Done"
if [ "$DRY" = 1 ]; then note "dry run: nothing was changed"; else note "packages were left installed"; fi
