#!/bin/sh
# Double-click this in Finder. Closing the Terminal window stops Tidy.
exec "$(dirname "$0")/tidy.sh"
