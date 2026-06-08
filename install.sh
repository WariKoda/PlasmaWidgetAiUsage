#!/usr/bin/env bash
# Install or upgrade the AI Usage plasmoid.
#   ./install.sh            -> current user only (~/.local/share/plasma/plasmoids)
#   ./install.sh --system   -> all users (/usr/share/plasma/plasmoids, needs sudo)
# Runtime state (cache, config, credentials) stays per-user either way.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$HERE/package"
ID="org.warikoda.aiusage"

if [[ "${1:-}" == "--system" ]]; then
    DEST="/usr/share/plasma/plasmoids/$ID"
    KPKG=(sudo kpackagetool6 --type Plasma/Applet --global)
else
    DEST="$HOME/.local/share/plasma/plasmoids/$ID"
    KPKG=(kpackagetool6 --type Plasma/Applet)
fi

if [[ -d "$DEST" ]]; then
    echo "Upgrading $ID ..."
    "${KPKG[@]}" --upgrade "$PKG"
else
    echo "Installing $ID ..."
    "${KPKG[@]}" --install "$PKG"
fi

echo
echo "Done. Add it via: right-click panel -> Add Widgets -> 'AI Usage'."
echo "If it does not show up, rescan the shell:"
echo "  kquitapp6 plasmashell && kstart plasmashell"
