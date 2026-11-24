# Changelog

## 1.0.0 (2026-11-25)

First public release.

- **Toggle.** `rice on` saves the look of the desktop, `rice off` restores it byte for byte.
  Panels run in one systemd slice with CPU and memory caps. Nothing autostarts.
- **Five flavours.** MONO, ROSE, NEON, AMBER and PAPER change colours and shapes: gaps, frames,
  buttons, spectrum, tile map, wallpaper, logo lettering. A flavour is one JSON file.
- **Live switching.** `rice flavour NAME` re-themes everything while it runs. Picker with a
  screenshot card per flavour, Meta+Alt+T to cycle, Meta+Alt+F for the picker, a panel button,
  KRunner entries.
- **ASCII deck.** Cover art drawn as characters by a shader, four art modes, transport and
  volume controls for keyboard, mouse and touch.
- **Reactive deck.** One audio analyser drives the art, the spectrum and the lyrics bar;
  `rice react off|subtle|full`, reduced-motion aware.
- **Lyrics bar.** Synced lyrics from a local `.lrc` file or LRCLIB, word by word, right-to-left
  aware.
- **Terminal showcase.** fastfetch with a per-flavour logo, block clock, chafa album art, six
  ASCII scenes; Enter gives a shell.
- **Classic layout.** cava and pipes.sh in place of the deck.
- **Tablet and pen.** Portrait tile maps, an optional tablet-mode panel service, and an
  optional DRAW / NAVIGATE switch for touch-only styluses in Rnote, Xournal++ and Krita.
- **Installer.** `install.sh` with `--dry-run`, backups of everything it replaces, and
  `uninstall.sh` that puts them back.

Tested on CachyOS with KDE Plasma 6.8 on Wayland, on one laptop. See "Tested, and not" in the
README for what has and has not been checked.
