#!/usr/bin/env bash
if ! command -v python3 &>/dev/null; then
    echo ""
    echo "Python 3 was not found."
    echo "Install it with your package manager, e.g.:"
    echo "  sudo apt install python3"
    echo ""
    exit 1
fi
cd "$(dirname "$0")"
exec python3 gui.pyw
