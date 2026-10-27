#!/usr/bin/env python3
"""Byte-level DMI-table patches for Huawei NBM-WXX9 audio support (AMD ACP/Renoir).

Background
----------
On Huawei MateBook laptops built around AMD ACP (Renoir), the analog audio
codec (ES8316) is wired to the ACP at PCI 0000:03:00.5.  Upstream kernels match
machines for this controller through DMI tables inside two kernel modules:

  * snd-acp-config        (module snd_acp_config)   — selects the ACP config
  * snd-acp-legacy-mach   (module snd_acp_legacy_mach) — registers the card

The DMI tables only know the sibling boards HVY-WXX9 / BOM-WXX9 / KLVL-WXX[W9],
never NBM-WXX9, so on a MateBook carrying board/product name "NBM-WXX9" the
driver probe fails early and silently with -ENODEV.

This script rewrites the DMI strings *in place* (all substitutions keep the
original byte length, so neighbouring structure fields stay intact):

  1. "HVY-WXX9" -> "NBM-WXX9"   (acp-config: makes the ACP driver probe pass)
  2. "BOM-WXX9" -> "NBM-WXX9"   (legacy-mach: BOM entry has the MCLK config
                                 suitable for this hardware family; the HVY
                                 entry produces loud corrupted audio)
  3. inside that same (first) NBM entry, the version slot at +0x50 bytes
     "M1010" -> the board version of *this* laptop (default "M1020",
     override with $NBM_VERSION; check yours:
       cat /sys/class/dmi/id/board_version /sys/class/dmi/id/product_version )

The scripts operate on a *decompressed* module image; re-compress afterwards
with zstd (see huawei-audio-patch.sh).

All work: upstream table layout assumed (as in Ubuntu kernels).  If the layout
ever changes, re-verify the +0x50 offset with `strings -t x` on the module.
"""
import os
import sys
from pathlib import Path

#: byte pairs applied to each decompressed module; must be same length
REPLACEMENTS = [
    (b"HVY-WXX9", b"NBM-WXX9"),
    (b"BOM-WXX9", b"NBM-WXX9"),
]

BOM_VERSION = os.environ.get("NBM_VERSION", "M1020").encode()
if len(BOM_VERSION) != 5:
    sys.exit(f"NBM_VERSION must be a 5-char string like M1020 (got {BOM_VERSION!r})")


def patch(path: Path) -> None:
    data = bytearray(path.read_bytes())
    changed = 0

    for old, new in REPLACEMENTS:
        n = data.count(old)
        if n:
            data[:] = data.replace(old, new)
            changed += n
            print(f"  {old.decode()!s:10} -> {new.decode()!s:10} x{n}")

    # Locate the BOM/first NBM DMI entry and retarget its version slot.
    i = bytes(data).find(b"NBM-WXX9")
    if i != -1 and bytes(data[i + 0x50 : i + 0x55]) == b"M1010":
        data[i + 0x50 : i + 0x55] = BOM_VERSION
        changed += 1
        print(f"  version slot at +0x50 of 0x{i:x}: M1010 -> {BOM_VERSION.decode()}")

    if changed == 0:
        print("  already patched (no-op)")
    path.write_bytes(bytes(data))


def main() -> None:
    targets = sys.argv[1:]
    if not targets:
        sys.exit(f"usage: {sys.argv[0]} module.ko [module.ko ...] (decompressed images)")
    for arg in targets:
        p = Path(arg)
        print(f"patching {p}")
        patch(p)


if __name__ == "__main__":
    main()
