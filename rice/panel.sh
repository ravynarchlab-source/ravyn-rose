#!/usr/bin/env bash
# One terminal panel of the rice: panel.sh matrix|lava|pipes|cava FLAVOUR_DIR RUN_DIR
#
# Runs the program with the options of the flavour in use. When `rice flavour` switches, rice sends this
# script SIGUSR1 and the program is started again with the new options inside the same window, so the
# window itself never closes. If the program ends on its own (you pressed q), the panel closes.
name=$1; FL=$2; RUN=$3
mkdir -p "$RUN"; echo $$ > "$RUN/panel-$name.pid"
again=0; child=
trap 'again=1; [ -n "$child" ] && kill "$child" 2>/dev/null' USR1
trap '[ -n "$child" ] && kill "$child" 2>/dev/null; exit 0' TERM INT HUP
while :; do
  again=0
  # shellcheck disable=SC1091
  . "$FL/env.sh"
  case "$name" in
    matrix) cmd=(cmatrix "${FL_MATRIX[@]}") ;;
    lava)   cmd=(lavat "${FL_LAVA[@]}") ;;
    pipes)  cmd=(pipes.sh "${FL_PIPES[@]}") ;;
    cava)   cmd=(cava -p "$FL/cava.conf") ;;
    *) echo "panel.sh: unknown panel $name" >&2; exit 2 ;;
  esac
  "${cmd[@]}" <&0 &                               # in the background so the signal above is handled at once
  child=$!
  while kill -0 "$child" 2>/dev/null; do wait "$child"; done
  child=
  [ "$again" = 1 ] || break
  stty sane 2>/dev/null; printf '\033[0m\033[?25h\033[2J\033[H'
done
rm -f "$RUN/panel-$name.pid"
