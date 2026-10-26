#!/bin/sh
# Only the package's isolated runtime can supply its CLI entry points.
exec /opt/foxlight/sovereign/python/bin/python3 -I -B /opt/foxlight/sovereign/native-cli.py "$(/usr/bin/basename "$0")" "$@"
