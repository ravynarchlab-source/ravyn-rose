# Shortcuts and ways to switch

Everything here is set up by `rice setup` (which `install.sh` runs). A global key is only
registered if it is free; if one is taken, setup leaves it alone and says so in
`~/.local/state/rice/log`. You can change any of them in System Settings > Keyboard > Shortcuts.

## Global keys

| Key | Action |
|---|---|
| Meta+Alt+N | rice on |
| Meta+Alt+O | rice off |
| Meta+Alt+T | next flavour (mono, rose, neon, amber, paper, and round again) |
| Meta+Alt+F | open the flavour picker |
| Meta+Alt+PgUp / PgDn | volume up / down (with the deck's on-screen read-out) |
| Meta+Alt+, / . | previous / next track |
| Meta+Alt+D | pen mode: DRAW / NAVIGATE (only with `./install.sh --pen`) |

All of them are in the Meta+Alt family on purpose. Meta+Shift+letter keys were tried first and
did not reach the launcher on the laptop this was built on.

## Switching flavour

Switching is live: no logout, and no window closes. The deck, the lyrics bar and the terminal
showcase fade to the new colours, the wallpaper cross-fades, frames and gaps change, the tiles
move to the new flavour's map, and cmatrix and lavat are started again inside their own
windows. On the laptop this was built on a switch takes about 0.7 to 1.1 seconds.

| Way | How |
|---|---|
| Key | Meta+Alt+T cycles; Meta+Alt+F opens the picker |
| Picker | five cards, each with a small screenshot. Click or tap one and it is applied while the picker stays open |
| Panel | `rice button flavour` puts a "Rice flavour" button on the panel (the installer does this) |
| Application menu | "Rice flavour" |
| KRunner | type `rice mono`, `rice rose`, `rice neon`, `rice amber`, `rice paper` or `rice next` |
| Command | `rice flavour mono`, `rice flavour next`, `rice flavour random`, `rice flavour list` |

With the rice off, any of these only stores your choice; it is used the next time the rice
turns on.

### In the picker

| Key | Action |
|---|---|
| 1 to 5 | switch to that flavour |
| Left / Right / Up / Down, Tab | move the highlight |
| Enter or Space | apply the highlighted flavour |
| Esc or Q | close |

The cards are sized for fingers and wrap to two columns on a portrait screen.
`rice flavour tui` opens a plain text version of the picker in a terminal.

## In the deck (when it has focus)

| Key | Action | Mouse or touch |
|---|---|---|
| Space | play / pause | the middle button |
| Left / Right | previous / next track | the outer buttons, or swipe across the art |
| `+` / `-` | volume | wheel on the art, or drag up and down |
| M | mute | tap the volume read-out |
| | seek | click the progress bar |
| Tab, or 1 to 4 | art mode: ramp, blocks, braille, tinted | tap the mode name |
| Z | larger characters | |
| F | fill or fit the cover | |

## In the terminal showcase

| Key | Action |
|---|---|
| Enter, Space or Q | leave the showcase for an ordinary shell |
| any other key | next page (clock, album art, scenes) |

## Commands

| Command | What it does |
|---|---|
| `rice on` / `rice off` / `rice toggle` | turn it on, put everything back, or whichever applies |
| `rice flavour [NAME\|next\|prev\|random\|list]` | switch flavour; no name opens the picker |
| `rice react on\|off\|subtle\|full` | how much the deck and the lyrics bar move with the music |
| `rice layout deck\|classic` | ASCII deck, or cava and pipes |
| `rice player pear\|fooyin\|auto` | which player gets the music tile |
| `rice button` / `rice button flavour` | add the toggle or the picker button to the panel |
| `rice status` | flavour, what is running, memory and CPU time |
