#!/bin/sh
# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

# Sets up shepherd for the current user. Safe to re-run: existing files are
# kept unless --force is given, and a second run changes nothing.
#
#   ./install.sh [--owner EMAIL] [--state-dir DIR] [--port N] [--config FILE]
#                [--non-interactive] [--force] [--service] [--no-poll]
#   ./install.sh --uninstall [--purge] [--config FILE]
#
# Flags fall back to SHEPHERD_OWNER, SHEPHERD_STATE_DIR, SHEPHERD_DASH_PORT
# and SHEPHERD_CONFIG. Nothing is installed system-wide and nothing needs
# sudo: the config goes to ~/.config/shepherd, a venv to the state
# directory, and --service adds a user-level launchd agent or systemd unit
# for the dashboard. --uninstall removes all of that except the state
# directory, which --purge removes as well.

set -eu

BUNDLE=$(cd "$(dirname "$0")" && pwd)
LABEL=local.shepherd.dash

owner=${SHEPHERD_OWNER:-}
state_dir=${SHEPHERD_STATE_DIR:-}
port=${SHEPHERD_DASH_PORT:-}
config=${SHEPHERD_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/shepherd/config.toml}
interactive=1
force=0
service=0
poll=1
uninstall=0
purge=0

usage() {
  sed -n '/^# Sets up/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case $1 in
    --owner) owner=${2:?--owner needs a value}; shift ;;
    --state-dir) state_dir=${2:?--state-dir needs a value}; shift ;;
    --port) port=${2:?--port needs a value}; shift ;;
    --config) config=${2:?--config needs a value}; shift ;;
    --non-interactive) interactive=0 ;;
    --force) force=1 ;;
    --service) service=1 ;;
    --no-poll) poll=0 ;;
    --uninstall) uninstall=1 ;;
    --purge) purge=1 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage 2 >&2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Writes stdin to $1 unless the file already has that content.
write_if_changed() {
  tmp="$1.tmp.$$"
  cat > "$tmp"
  if [ -f "$1" ] && cmp -s "$tmp" "$1"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$1"
  return 0
}

ask() {
  # ask VAR PROMPT DEFAULT
  if [ "$interactive" = 1 ] && [ -r /dev/tty ]; then
    printf '%s [%s]: ' "$2" "$3" > /dev/tty
    read -r answer < /dev/tty || answer=
    eval "$1=\${answer:-\$3}"
  else
    eval "$1=\$3"
  fi
}

# Prerequisites ---------------------------------------------------------------

python=${PYTHON:-python3}
missing=
command -v "$python" > /dev/null 2>&1 || missing="$missing python3>=3.11"
if [ -z "$missing" ] && ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  missing="$missing python3>=3.11(found $("$python" -V 2>&1))"
fi
command -v git > /dev/null 2>&1 || missing="$missing git"
if [ -n "$missing" ] && [ "$uninstall" = 0 ]; then
  die "missing:$missing. Install them (set PYTHON to pick an interpreter) and re-run."
fi
if [ "$uninstall" = 0 ] && ! command -v git-cl > /dev/null 2>&1; then
  say "warning: 'git cl' not found; add depot_tools to PATH to upload CLs."
fi

# Reads a value from the effective config through the package itself.
cfg_value() {
  SHEPHERD_CONFIG=$config PYTHONPATH=$BUNDLE "$python" -c \
    "from shepherd import config; print(config.load().$1)"
}

service_file() {
  case $(uname -s) in
    Darwin) echo "$HOME/Library/LaunchAgents/$LABEL.plist" ;;
    Linux) echo "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/shepherd-dash.service" ;;
    *) echo "" ;;
  esac
}

# Uninstall -------------------------------------------------------------------

if [ "$uninstall" = 1 ]; then
  if [ -f "$config" ]; then
    state_dir=$(cfg_value state_dir)
  else
    state_dir=${state_dir:-${XDG_STATE_HOME:-$HOME/.local/state}/shepherd}
  fi
  unit=$(service_file)
  if [ -n "$unit" ] && [ -f "$unit" ]; then
    case $(uname -s) in
      Darwin) launchctl bootout "gui/$(id -u)" "$unit" 2> /dev/null || true ;;
      Linux) systemctl --user disable --now shepherd-dash.service 2> /dev/null || true ;;
    esac
    rm -f "$unit"
    say "removed $unit"
  fi
  if [ -d "$state_dir/venv" ]; then
    rm -rf "$state_dir/venv"
    say "removed $state_dir/venv"
  fi
  if [ -f "$config" ]; then
    rm -f "$config"
    rmdir "$(dirname "$config")" 2> /dev/null || true
    say "removed $config"
  fi
  if [ "$purge" = 1 ] && [ -d "$state_dir" ]; then
    rm -rf "$state_dir"
    say "removed $state_dir"
  elif [ -d "$state_dir" ]; then
    say "kept $state_dir (--purge removes it)"
  fi
  exit 0
fi

# Config ----------------------------------------------------------------------

if [ -f "$config" ] && [ "$force" = 0 ]; then
  say "config: keeping $config (--force rewrites it)"
else
  ask owner "Gerrit account whose CLs to track" "${owner:-}"
  [ -n "$owner" ] || die "no owner given; use --owner EMAIL"
  ask state_dir "State directory" "${state_dir:-${XDG_STATE_HOME:-$HOME/.local/state}/shepherd}"
  ask port "Dashboard port" "${port:-8787}"
  case $port in
    ''|*[!0-9]*) die "port must be a number: $port" ;;
  esac
  case $owner in
    *[\"\\]*) die "owner must not contain quotes or backslashes" ;;
  esac
  case $state_dir in
    *[\"\\]*) die "state directory must not contain quotes or backslashes" ;;
  esac
  mkdir -p "$(dirname "$config")"
  if sed -e "s|^owner = \"you@example.com\"|owner = \"$owner\"|" \
      -e "s|^state_dir = .*|state_dir = \"$state_dir\"|" \
      -e "s|^port = .*|port = $port|" \
      -e '1,/^$/d' "$BUNDLE/config.example.toml" | write_if_changed "$config"; then
    say "config: wrote $config"
  else
    say "config: $config is up to date"
  fi
fi

state_dir=$(cfg_value state_dir) || die "cannot read $config"
watch_dir=$(cfg_value watch_dir)
data_dir=$(cfg_value data_dir)
dash_url="http://localhost:$(cfg_value dash_port)/"

# State -----------------------------------------------------------------------

mkdir -p "$state_dir" "$watch_dir" "$data_dir"
[ -f "$data_dir/cls.json" ] || printf '{}\n' > "$data_dir/cls.json"
[ -f "$data_dir/requests.jsonl" ] || : > "$data_dir/requests.jsonl"
say "state: $state_dir"

# Venv ------------------------------------------------------------------------

# The package is stdlib only, so the venv just needs to find the bundle: a
# .pth file adds it to sys.path, as an editable install would, without
# fetching a build backend.
venv=$state_dir/venv
if [ ! -x "$venv/bin/python" ]; then
  "$python" -m venv "$venv"
  say "venv: created $venv"
fi
site=$("$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
printf '%s\n' "$BUNDLE" | write_if_changed "$site/shepherd-bundle.pth" \
  && say "venv: linked $BUNDLE"
cat <<EOF | write_if_changed "$venv/bin/shepherd" && say "venv: wrote $venv/bin/shepherd"
#!/bin/sh
SHEPHERD_CONFIG=\${SHEPHERD_CONFIG:-$config}
export SHEPHERD_CONFIG
exec "$venv/bin/python" -m shepherd "\$@"
EOF
chmod +x "$venv/bin/shepherd"
shepherd=$venv/bin/shepherd

# Seed poll -------------------------------------------------------------------

if [ "$poll" = 1 ]; then
  set +e
  out=$("$shepherd" watch --once --no-notify 2>&1)
  rc=$?
  set -e
  case $rc in
    0) say "gerrit: polled; $(cfg_value state)" ;;
    2) say "gerrit: a watcher is already running; skipped the seed poll" ;;
    *) printf '%s\n' "$out" >&2; die "Gerrit poll failed (exit $rc)" ;;
  esac
fi

# Service ---------------------------------------------------------------------

if [ "$service" = 1 ]; then
  unit=$(service_file)
  [ -n "$unit" ] || die "--service supports macOS (launchd) and Linux (systemd)"
  mkdir -p "$(dirname "$unit")"
  case $(uname -s) in
    Darwin)
      if cat <<EOF | write_if_changed "$unit"; then
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$shepherd</string><string>dash</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$state_dir/dash.log</string>
</dict>
</plist>
EOF
        launchctl bootout "gui/$(id -u)" "$unit" 2> /dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$unit"
        say "service: installed $unit"
      else
        say "service: $unit is up to date"
      fi
      ;;
    Linux)
      if cat <<EOF | write_if_changed "$unit"; then
[Unit]
Description=shepherd dashboard

[Service]
ExecStart=$shepherd dash
Restart=on-failure

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now shepherd-dash.service
        say "service: installed $unit"
      else
        say "service: $unit is up to date"
      fi
      ;;
  esac
fi

# Next steps ------------------------------------------------------------------

say ""
say "Next steps:"
if [ "$service" = 1 ]; then
  say "  dashboard:  $dash_url (running as a user service)"
else
  say "  dashboard:  $shepherd dash    then open $dash_url"
fi
say "  agent:      point your coding agent at $BUNDLE/prompt/shepherd.md"
say "              (plus $BUNDLE/adapters/<harness>.md if there is one);"
say "              it runs '$shepherd wait' in a loop."
say "  PATH:       add $venv/bin to PATH to type 'shepherd'."
