#!/usr/bin/env bash
# ravyn-rose installer: KDE Plasma 6 on Wayland, Arch or CachyOS.
#
#   ./install.sh --dry-run     print every step, change nothing
#   ./install.sh               install packages, copy the files, set up the toggle
#
#   --touch       also install the tablet-mode panel service (2-in-1 laptops)
#   --pen         also install pen mode: a DRAW / NAVIGATE switch for touch-only styluses
#                 (pen/install.sh installs that module alone)
#   --no-deps     do not install packages (only report what is missing)
#   --no-button   do not add the "Rice" button to the panel
#   --no-setup    copy files only; run `rice setup` yourself later
#
# Nothing here turns the rice on and nothing autostarts. Files it replaces are copied to
# ~/.local/state/ravyn-rose/backup first, and uninstall.sh puts them back.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY=0; DEPS=1; TOUCH=0; PEN=0; BUTTON=1; SETUP=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --touch) TOUCH=1 ;;
    --pen) PEN=1 ;;
    --no-deps) DEPS=0 ;;
    --no-button) BUTTON=0 ;;
    --no-setup) SETUP=0; BUTTON=0 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

INSTALLED="$HOME/.local/share/rice"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
UNITS="$HOME/.config/systemd/user"
FONTS="$HOME/.local/share/fonts/rice"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/ravyn-rose"
BACKUP="$STATE/backup"
MANIFEST="$STATE/manifest.tsv"

# Monocraft is not shipped in this repository; it comes from its own release page (SIL OFL 1.1).
FONT_URL="https://github.com/IdreesInc/Monocraft/releases/download/v4.2.1/Monocraft-otf.zip"
FONT_SHA256="e623b72f1021062ad0156cc41f54b108e70bd35e2b295127475bb572d8ade61d"

# What each group is for is written up in DEPENDENCIES.md.
PKG_CORE=(alacritty qt6-declarative qt6-tools playerctl python libnotify rsync fontconfig curl unzip
          ttf-cascadia-code-nerd noto-fonts)
PKG_DECK=(pyside6 python-pillow qt6-shadertools cava wireplumber)
PKG_PANELS=(cmatrix fish fastfetch chafa fooyin)
PKG_EXTRA=(lavat pipes.sh)          # AUR on Arch; the rice simply skips a panel whose program is missing

step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then printf '   would run:'; printf ' %q' "$@"; printf '\n'; else "$@"; fi
}

# ---------------------------------------------------------------- checks
step "Checking this machine"
[ "$(id -u)" -ne 0 ] || { echo "Run this as your normal user, not as root." >&2; exit 1; }
command -v pacman >/dev/null || { echo "pacman not found: this installer is for Arch-based systems." >&2; exit 1; }
plasma=$(pacman -Q plasma-workspace 2>/dev/null | awk '{print $2}') || true
case "$plasma" in
  6.*) note "Plasma $plasma" ;;
  "") note "WARNING: plasma-workspace is not installed. The rice needs KDE Plasma 6." ;;
  *) note "WARNING: Plasma $plasma found. The rice was built for Plasma 6." ;;
esac
[ "${XDG_SESSION_TYPE:-}" = wayland ] || note "WARNING: this is not a Wayland session (XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset}). Untested on X11."
if [ -f "${XDG_STATE_HOME:-$HOME/.local/state}/rice/active" ]; then
  echo "The rice is on right now. Run 'rice off' first, then install again." >&2
  [ "$DRY" = 1 ] || exit 1
fi

# ---------------------------------------------------------------- packages
step "Packages"
from_repo=(); from_aur=()
for p in "${PKG_CORE[@]}" "${PKG_DECK[@]}" "${PKG_PANELS[@]}" "${PKG_EXTRA[@]}"; do
  pacman -Qq "$p" >/dev/null 2>&1 && continue
  if pacman -Si "$p" >/dev/null 2>&1; then from_repo+=("$p"); else from_aur+=("$p"); fi
done
if [ ${#from_repo[@]} -eq 0 ] && [ ${#from_aur[@]} -eq 0 ]; then
  note "everything is already installed"
elif [ "$DEPS" = 0 ]; then
  [ ${#from_repo[@]} -gt 0 ] && note "missing (repositories): ${from_repo[*]}"
  [ ${#from_aur[@]} -gt 0 ] && note "missing (AUR): ${from_aur[*]}"
  note "--no-deps given: not installing them"
else
  if [ ${#from_repo[@]} -gt 0 ]; then
    note "from the repositories: ${from_repo[*]}"
    run sudo pacman -S --needed "${from_repo[@]}"
  fi
  if [ ${#from_aur[@]} -gt 0 ]; then
    helper=$(command -v paru || command -v yay || true)
    if [ -n "$helper" ]; then
      note "from the AUR, with ${helper##*/}: ${from_aur[*]}"
      run "$helper" -S --needed "${from_aur[@]}"
    else
      note "not in your repositories and no AUR helper (paru, yay) found: ${from_aur[*]}"
      note "the rice works without them; the matching panel stays closed until you install them"
    fi
  fi
fi

# ---------------------------------------------------------------- backup
step "Backing up what will be replaced"
targets=("$INSTALLED" "$BIN/rice" "$FONTS" "$UNITS/rice.slice")
for n in on off toggle flavour deck vol-up vol-down prev next; do targets+=("$APPS/rice-$n.desktop"); done
[ "$TOUCH" = 1 ] && targets+=("$BIN/touch-mode-panel" "$UNITS/touch-mode-panel.service")
if [ -f "$MANIFEST" ]; then
  note "earlier install found: keeping the first backup in $BACKUP"
  if [ "$TOUCH" = 1 ] && ! grep -q "touch-mode-panel$" "$MANIFEST"; then      # touch part added later
    for t in "$BIN/touch-mode-panel" "$UNITS/touch-mode-panel.service"; do
      if [ -e "$t" ]; then run mkdir -p "$BACKUP/$(dirname "${t#"$HOME"/}")"; run cp -a "$t" "$BACKUP/${t#"$HOME"/}"; state=existed; else state=absent; fi
      if [ "$DRY" = 1 ]; then note "would record: $state ${t#"$HOME"/}"; else printf '%s\t%s\n' "$state" "${t#"$HOME"/}" >> "$MANIFEST"; fi
    done
  fi
else
  run mkdir -p "$BACKUP" "$STATE/reference"
  for t in "${targets[@]}"; do
    rel=${t#"$HOME"/}
    if [ -e "$t" ] || [ -L "$t" ]; then
      note "saving ~/$rel"
      run mkdir -p "$BACKUP/$(dirname "$rel")"
      run cp -a "$t" "$BACKUP/$rel"
      state=existed
    else
      state=absent
    fi
    if [ "$DRY" = 1 ]; then :; else printf '%s\t%s\n' "$state" "$rel" >> "$MANIFEST"; fi
  done
  # Reference copies of the KDE files the toggle and its shortcuts write to. They are not restored
  # automatically (rice off already restores the look byte for byte); they are there if you want to compare.
  for f in kdeglobals kwinrulesrc breezerc kglobalshortcutsrc plasma-org.kde.plasma.desktop-appletsrc; do
    [ -f "$HOME/.config/$f" ] && run cp -p "$HOME/.config/$f" "$STATE/reference/$f"
  done
  note "backup: $BACKUP"
fi

# ---------------------------------------------------------------- files
step "Copying the rice to $INSTALLED"
run rm -rf "$INSTALLED"
run mkdir -p "$INSTALLED"
run cp -a "$HERE/rice/." "$INSTALLED/"
run chmod 755 "$INSTALLED/rice" "$INSTALLED/flavour.py" "$INSTALLED/lyrics.py" "$INSTALLED/deck/deck.py" \
  "$INSTALLED/deck/ctl.py" "$INSTALLED/showcase/showcase.py"

step "Monocraft font (downloaded from its release page, not shipped here)"
if [ -f "$FONTS/Monocraft.otf" ] && [ -f "$FONTS/Monocraft-Bold.otf" ]; then
  note "already there"
elif [ "$DRY" = 1 ]; then
  note "would download $FONT_URL"
  note "would check its SHA-256 and unpack Monocraft.otf and Monocraft-Bold.otf into $FONTS"
else
  tmp=$(mktemp -d)
  if curl -fsSL -o "$tmp/monocraft.zip" "$FONT_URL" && echo "$FONT_SHA256  $tmp/monocraft.zip" | sha256sum -c --quiet -; then
    unzip -q -o -j "$tmp/monocraft.zip" "Monocraft-otf/Monocraft.otf" "Monocraft-otf/weights/Monocraft-Bold.otf" -d "$tmp/out"
    mkdir -p "$FONTS"
    install -m644 "$tmp/out/Monocraft.otf" "$tmp/out/Monocraft-Bold.otf" "$FONTS/"
    fc-cache -f >/dev/null 2>&1 || true
    note "installed to $FONTS"
  else
    note "WARNING: download failed or the checksum did not match. Nothing was installed."
    note "The rice still runs; text falls back to your default monospace font."
  fi
  rm -rf "$tmp"
fi

# ---------------------------------------------------------------- toggle
if [ "$SETUP" = 1 ]; then
  step "Setting up the toggle (launchers, shortcuts, resource limits)"
  if [ "$DRY" = 1 ] || qdbus6 org.kde.KWin /KWin >/dev/null 2>&1; then
    run "$INSTALLED/rice" setup
    [ "$BUTTON" = 1 ] && run "$INSTALLED/rice" button
  else
    note "No running Plasma session found. After you log in to Plasma, run:"
    note "  $INSTALLED/rice setup && $INSTALLED/rice button"
  fi
else
  step "Skipping setup (--no-setup)"
  note "later: $INSTALLED/rice setup"
fi

# ---------------------------------------------------------------- touch
if [ "$TOUCH" = 1 ]; then
  step "Tablet-mode panel service"
  run install -Dm755 "$HERE/touch/touch-mode-panel" "$BIN/touch-mode-panel"
  run install -Dm644 "$HERE/touch/touch-mode-panel.service" "$UNITS/touch-mode-panel.service"
  run systemctl --user daemon-reload || true
  run systemctl --user enable --now touch-mode-panel.service \
    || note "could not start it now; run: systemctl --user enable --now touch-mode-panel.service"
fi

# ---------------------------------------------------------------- pen
if [ "$PEN" = 1 ]; then
  pen_args=()
  [ "$DRY" = 1 ] && pen_args+=(--dry-run)
  [ "$DEPS" = 0 ] && pen_args+=(--no-deps)
  "$HERE/pen/install.sh" "${pen_args[@]}" || note "pen mode was not installed (see above); run pen/install.sh again later"
fi

step "Done"
if [ "$DRY" = 1 ]; then
  note "dry run: nothing was changed"
else
  note "rice on      turn it on        (also: the Rice panel button, or Meta+Alt+N)"
  note "rice flavour pick one of mono, rose, neon, amber, paper (or: rice flavour random)"
  note "rice off     put everything back as it was (Meta+Alt+O)"
  case ":$PATH:" in *":$BIN:"*) ;; *) note "note: $BIN is not in your PATH; use $BIN/rice or add it" ;; esac
  note "remove with: ./uninstall.sh"
fi
