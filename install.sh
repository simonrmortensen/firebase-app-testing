#!/bin/sh
# Install the commands by symlinking them into ~/.local/bin, so `git pull` updates them.
# Also installs mitmproxy (for ios-testing) with uv, if it is not there yet.
#   ./install.sh              install
#   ./install.sh --uninstall  remove the symlinks (mitmproxy is left alone)
set -e
REPO=$(cd "$(dirname "$0")" && pwd)
BIN="$HOME/.local/bin"
COMMANDS="android-testing/android-testing android-testing/android-testing-events ios-testing/ios-testing"

if [ "$1" = "--uninstall" ]; then
  for cmd in $COMMANDS; do
    link="$BIN/$(basename "$cmd")"
    [ -L "$link" ] && rm "$link" && echo "removed $link"
  done
  exit 0
fi

mkdir -p "$BIN"
for cmd in $COMMANDS; do
  chmod +x "$REPO/$cmd"
  ln -sf "$REPO/$cmd" "$BIN/$(basename "$cmd")"
  echo "linked $BIN/$(basename "$cmd")"
done

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "note: $BIN is not on your PATH; add it to your shell profile" ;;
esac

if command -v mitmdump >/dev/null 2>&1; then
  echo "mitmproxy already installed"
elif command -v uv >/dev/null 2>&1; then
  # Only packages at least 3 days old, as a guard against freshly published malicious releases.
  uv tool install mitmproxy --exclude-newer "3 days"
else
  echo "note: uv not found, so mitmproxy was not installed (ios-testing needs it): brew install uv"
fi
echo "done; see AGENTS.md for the Android prerequisites"
