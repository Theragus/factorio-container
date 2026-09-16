#!/usr/bin/env bash
# Healthy == the game port is bound. Read straight from /proc so the image
# needs no networking tools.
set -euo pipefail

port_bound() {
    local proc_file="$1" port="$2" hex
    hex="$(printf '%04X' "${port}")"
    awk -v want=":${hex}" 'NR > 1 { split($2, a, ":"); if (":" a[2] == want) found = 1 } END { exit !found }' \
        "${proc_file}"
}

game_port="${PORT:-34197}"

if port_bound /proc/net/udp "${game_port}" || port_bound /proc/net/udp6 "${game_port}" 2>/dev/null; then
    exit 0
fi

echo "Factorio game port ${game_port}/udp is not bound" >&2
exit 1
