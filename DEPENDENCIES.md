# Dependencies

`install.sh` checks each package below, installs the missing ones from your repositories with
`pacman`, and hands anything it cannot find there to `paru` or `yay`. Package names are the
Arch ones. The "found in" column is where they came from on CachyOS when this was written.

## Needed for the toggle and the lyrics bar

| Package | Why | Found in |
|---|---|---|
| `plasma-workspace`, `kwin`, `kconfig` | Plasma 6 itself: colour schemes, wallpaper scripting, window rules, KWin scripts | repositories (part of any Plasma install) |
| `qt6-tools` | `qdbus6`, used to talk to KWin and Plasma | repositories |
| `qt6-declarative` | `qml6`, which runs the lyrics window | repositories |
| `python` | lyrics backend | repositories |
| `playerctl` | reads the playing track over MPRIS | repositories |
| `alacritty` | hosts every terminal panel | repositories |
| `ttf-cascadia-code-nerd` | font in the terminal panels | repositories |
| `noto-fonts` | Noto Kufi Arabic for right-to-left lyric lines | repositories |
| `libnotify` | the "on" and "off" notifications | repositories |
| `rsync`, `fontconfig`, `curl`, `unzip` | restoring GTK assets; installing the font | repositories |

`systemd`, `glib2` (`gdbus`), `dbus` and `util-linux` (`flock`) are used too and are part of
every Arch install.

## Needed for the ASCII deck

| Package | Why | Found in |
|---|---|---|
| `pyside6` | the deck window and its backend | repositories |
| `python-pillow` | crops and sharpens the cover art, builds the glyph atlas | repositories |
| `qt6-shadertools` | `qsb`, which compiles the two shaders on your machine | repositories |
| `cava` | spectrum data (raw mode) and the classic visualizer | repositories |
| `wireplumber` | `wpctl`, for volume and mute | repositories |

## Panels

| Package | Panel | Found in |
|---|---|---|
| `cmatrix` | matrix | repositories |
| `fastfetch` | system facts in the terminal showcase | repositories |
| `chafa` | album art in the terminal showcase (without it a plainer built-in drawing is used) | repositories |
| `fish` | the shell behind the showcase (falls back to your login shell) | repositories |
| `fooyin` | music player | repositories |
| `lavat` | lava | AUR |
| `pipes.sh` | pipes (classic layout) | AUR |

A panel whose program is missing is skipped with a line in the log. The rest still runs.

## Pen mode (`--pen`)

| Package | Why | Found in |
|---|---|---|
| `pyside6`, `qt6-declarative` | the tray icon and the on-screen pill | repositories |
| `layer-shell-qt` | keeps the pill above other windows without taking focus (part of any Plasma install) | repositories |
| `glib2`, `kconfig`, `qt6-tools` | `gdbus` and `gsettings`, `kwriteconfig6`, D-Bus to Plasma and to the running apps | repositories |
| `python-pyqt6` | Krita's Python plugins, which the Krita side of the switch is. Only asked for when Krita is installed | repositories |
| `rnote`, `xournalpp`, `krita` | the apps themselves. Not installed by `install.sh`; a missing one is skipped | repositories |

## Optional

| Package | For | Found in |
|---|---|---|
| `pear-desktop-bin` | Pear Desktop as the music player. Not installed by `install.sh` | AUR |
| `python-numpy` | redrawing the wallpapers with `tools/wallpapers.py` | repositories |
| `python-numpy`, `flac` | `tools/make_test_track.py`, a generated test tone with an `.lrc` file | repositories |

## Not a package

| Thing | Where it comes from |
|---|---|
| Monocraft font, v4.2.1 | Downloaded by `install.sh` from <https://github.com/IdreesInc/Monocraft/releases> and checked against a SHA-256. Without it the deck falls back to your default monospace font. |
