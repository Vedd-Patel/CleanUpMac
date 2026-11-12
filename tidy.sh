#!/bin/sh
# Starts the server and opens the page. Pass --no-browser to skip the browser,
# --port N to pick a port, -v to log requests.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is needed. On a Mac, running 'xcode-select --install' gives you one." >&2
    exit 1
fi

exec python3 -m tidy "$@"
