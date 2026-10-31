#!/bin/bash
# Builds Backchannel.app from mac/Backchannel.swift and installs it in /Applications
# (or ~/Applications if /Applications isn't writable). Needs Xcode Command Line Tools.
# The app runs the server from this repo, so keep the repo where it is after building.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO="$(pwd)"
APP="$REPO/mac/build/Backchannel.app"

if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "Building the app needs Apple's command line tools. Install them with:"
  echo "  xcode-select --install"
  exit 1
fi
if [[ ! -x "$REPO/.venv/bin/python" ]]; then
  echo "Run ./backchannel setup first (the app starts the server from .venv)."
  exit 1
fi

echo "Compiling Backchannel.app…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
xcrun swiftc -O -parse-as-library -swift-version 5 \
  -target "$(uname -m)-apple-macos13.0" \
  -framework Cocoa -framework WebKit -framework UserNotifications \
  -framework ServiceManagement -framework UniformTypeIdentifiers \
  "$REPO/mac/Backchannel.swift" -o "$APP/Contents/MacOS/Backchannel"

# Escape the repo path for the plist (XML) and for sed.
REPO_XML=$(printf '%s' "$REPO" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')
REPO_SED=$(printf '%s' "$REPO_XML" | sed -e 's/[\/&|]/\\&/g')
sed "s|__REPO__|$REPO_SED|" "$REPO/mac/Info.plist" > "$APP/Contents/Info.plist"
cp "$REPO/mac/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "(codesign failed; the app should still run locally)"

DEST=/Applications
[[ -w "$DEST" ]] || DEST="$HOME/Applications"
mkdir -p "$DEST"

# Quit a running copy so the new build replaces it (it stops the server it started).
if pgrep -xq Backchannel; then
  osascript -e 'tell application "Backchannel" to quit' >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6; do pgrep -xq Backchannel || break; sleep 0.5; done
fi
rm -rf "$DEST/Backchannel.app"
cp -R "$APP" "$DEST/"
echo "Installed $DEST/Backchannel.app"
echo "Opening it now. To keep it in your Dock: right-click its Dock icon › Options › Keep in Dock."
open "$DEST/Backchannel.app"
