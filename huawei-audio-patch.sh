#!/bin/bash
# Re-apply the Huawei NBM-WXX9 audio byte-patches to installed kernel sound
# modules, sign them with the local MOK key, and put them back in place.
#
# Usage:  huawei-audio-patch.sh [kernel-release ...]
#         (no args: every installed kernel that has the target modules)
#
#
# This script must run as root (it writes to /usr/lib/modules).
# It expects, next to itself:
#   bytepatch.py     — the actual byte patch definitions
#   mok/mok.priv
#   mok/mok.crt      — your enrolled MOK key (see README.md, "Signing")
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BYTEPATCH="$SCRIPT_DIR/bytepatch.py"
KEY=${MOKKEY:-$SCRIPT_DIR/mok/mok.priv}
CRT=${MOKCRT:-$SCRIPT_DIR/mok/mok.crt}
MODULES=(snd-acp-config.ko.zst snd-acp-legacy-mach.ko.zst)

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
[ -f "$BYTEPATCH" ] || { echo "bytepatch.py not found at $BYTEPATCH" >&2; exit 1; }
[ -f "$KEY" ] || { echo "MOK key not found at $KEY" >&2; exit 1; }
[ -f "$CRT" ] || { echo "MOK cert not found at $CRT" >&2; exit 1; }
SIGN=$(find /usr/src -name sign-file -type f 2>/dev/null | sort | tail -1)
[ -n "${SIGN:-}" ] || { echo "sign-file not found — install linux-headers" >&2; exit 1; }

patch_one() {
    local kver=$1 mod=$2 target dir tmp
    target=$(find "/usr/lib/modules/$kver" -name "$mod" 2>/dev/null | head -1 || true)
    [ -n "$target" ] || return 0
    dir=$(dirname "$target")

    # keep a pristine copy once
    [ -f "$dir/${mod}.orig" ] || cp "$target" "$dir/${mod}.orig"

    tmp=$(mktemp)
    zstd -dc "$target" > "$tmp"
    echo "==> $kver / $mod"
    python3 "$BYTEPATCH" "$tmp"

    # strip any previous signature, sign with our MOK key
    "$SIGN" -d sha512 "$KEY" "$CRT" "$tmp" >/dev/null 2>&1 || true
    "$SIGN" sha512 "$KEY" "$CRT" "$tmp"
    zstd -f -q "$tmp" -o "$target"
    rm -f "$tmp"
    echo "    -> $target"
}

if [ $# -ge 1 ]; then
    KERNELS=("$@")
else
    KERNELS=($(for k in /usr/lib/modules/*/; do
        for m in "${MODULES[@]}"; do
            find "$k" -name "$m" -print -quit 2>/dev/null | grep -q . && basename "$k" && break
        done
    done))
fi

for k in "${KERNELS[@]}"; do
    for m in "${MODULES[@]}"; do
        patch_one "$k" "$m"
    done
done

echo "done — reboot to pick up changes in the running kernel"
