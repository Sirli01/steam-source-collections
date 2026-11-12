#!/usr/bin/env sh
# macOS / Linux launcher. On Windows, double-click the Chinese .bat instead.
cd "$(dirname "$0")" || exit 1
exec python3 -m steam_source_collections.gui
