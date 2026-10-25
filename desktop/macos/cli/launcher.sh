#!/bin/sh
# Resolve this installed command, including an operator-created PATH symlink.
set -eu
cli_path=$0
case "$cli_path" in
    /*) ;;
    *) cli_path="$(/bin/pwd -P)/$cli_path" ;;
esac
cli_hops=0
while [ -L "$cli_path" ]; do
    cli_hops=$((cli_hops + 1))
    if [ "$cli_hops" -gt 32 ]; then
        printf '%s\n' 'Sovereign command has too many symbolic links.' >&2
        exit 2
    fi
    cli_parent=$(/usr/bin/dirname "$cli_path")
    cli_target=$(/usr/bin/readlink "$cli_path")
    case "$cli_target" in
        /*) cli_path=$cli_target ;;
        *) cli_path="$cli_parent/$cli_target" ;;
    esac
done
cli_directory=$(CDPATH= cd -P "$(/usr/bin/dirname "$cli_path")" && /bin/pwd -P)
cli_command=$(/usr/bin/basename "$cli_path")
cli_resources="$cli_directory/.."
if [ ! -x "$cli_resources/python/bin/python3" ] || [ ! -f "$cli_resources/native-cli.py" ]; then
    printf '%s\n' 'The bundled Sovereign runtime is missing. Reinstall the application.' >&2
    exit 2
fi
# Python ignores ambient Python configuration and never writes into the seal.
unset __PYVENV_LAUNCHER__
exec "$cli_resources/python/bin/python3" -I -B "$cli_resources/native-cli.py" "$cli_command" "$@"
