#!/bin/sh
# GI follows the distro's security updates; model ownership uses bundled Python.
exec /usr/bin/python3 -I -B /opt/foxlight/sovereign/sovereign-linux.py "$@"
