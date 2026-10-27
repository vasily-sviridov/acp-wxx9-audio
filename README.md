# acp-wxx9-audio

**Analog audio (speakers / headphones / headset mic) for Huawei MateBook laptops on AMD
Renoir, board family `NBM-WXX9`, via byte-patched kernel sound modules.**

> Tested on: Ubuntu 26.10 (`kernel 7.3.0-9-generic`), Huawei MateBook D
> (`board_name = NBM-WXX9-PCB-B2`, `board_version = M1020`), codec ES8316 on the ACP
> at PCI `0000:03:00.5` (`1022:15e2`).

## TL;DR

On these laptops the analog codec is connected to the AMD Audio Coprocessor
(`snd_acp_pci`). Upstream kernels select the machine configuration for that controller
by matching DMI tables inside `snd-acp-config` and `snd-acp-legacy-mach`, and the
tables list every Huawei board **except** this one (they know `HVY-WXX9`, `BOM-WXX9`,
`KLVL-WXX[W9]` — but not `NBM-WXX9`). Without a match, the driver probe fails early
and silently with `-ENODEV`, leaving only HDMI audio and the DMIC microphone.

The fix is a same-length byte substitution of the DMI strings inside those two kernel
modules, plus re-signing them with your own MOK key (distro kernels enforce module
signatures). A (`/etc/kernel/postinst.d`) hook keeps the patch re-applied across
kernel updates.

Quick start:

```bash
# stop the legacy DMIC-only driver from grabbing the ACP device
sudo tee /etc/modprobe.d/huawei-audio.conf <<'EOF'
blacklist snd_rn_pci_acp3x
blacklist snd_acp3x_rn
EOF

# generate + enroll a local MOK signing key (once, see "Signing your modules")
openssl req -x509 -nodes -new -config mok/mok.cnf -keyout mok/mok.priv -out mok/mok.crt -days 36500
openssl x509 -in mok/mok.crt -outform DER -out mok/mok.der
sudo mokutil --import mok/mok.der
sudo reboot    # blue-screen MokManager: Enroll MOK -> Continue -> Yes -> password -> Reboot

# patch + sign the sound modules of every installed kernel
sudo ./huawei-audio-patch.sh

# survive kernel updates
sudo install -m 755 huawei-audio-patch.sh /usr/local/sbin/huawei-audio-patch
sudo tee /etc/kernel/postinst.d/zz-huawei-audio-patch >/dev/null <<'EOF'
#!/bin/sh
exec /usr/local/sbin/huawei-audio-patch "$@"
EOF
sudo chmod +x /etc/kernel/postinst.d/zz-huawei-audio-patch
```

## Symptoms

- `aplay -l` shows only `card 0 … HDMI 0` (from `03:00.1`, the HDMI/DP controller) and,
  separately, only a DMIC capture device (from the legacy driver `snd_rn_pci_acp3x`).
- The sound settings applet lists no speakers and no headphone jack.
- Kernel log shows nothing from the ACP sound path — the driver probes and exits
  silently before printing anything.

## Hardware background

`lspci -d 1022:` shows two audio PCI functions:

| Device       | Function   | What it carries                          |
|--------------|------------|------------------------------------------|
| `1022:1637`  | `03:00.1`  | HDMI/DP HDA controller (`snd_hda_intel`) |
| `1022:15e2`  | `03:00.5`  | **Audio Coprocessor (ACP)** — analog codec ES8316 on I2S + I2C |

Routes to usable analog audio on Renoir, and why each of them fails on this board:

| Route | Result on this laptop |
|---|---|
| Legacy driver `snd_rn_pci_acp3x` on `03:00.5` | Works, but is **DMIC-only**; no speakers/headphones |
| `snd_hda_intel` on `03:00.5` | Driver's PCI ID table does not contain `1022:15e2`; a manual bind returns `No such device` |
| SOF (`snd_sof_amd_acp`) | Requires AMD firmware (`amd/sof-rn.ri`) and topologies; the distro's `firmware-sof-signed` ships Intel files only |
| New ACP PCI driver `snd_acp_pci` | Correct driver — but its DMI machine table **does not list `NBM-WXX9`**, so the probe exits with `-ENODEV` before doing anything |

The winning route is the last one, once the DMI table is patched.

## Root cause

Both `snd-acp-config` (module `snd_acp_config`, exports `snd_amd_acp_find_config()`)
and `snd-acp-legacy-mach` (the machine driver that actually registers the card
`acp3x-es83xx`) contain a `dmi_check_system()` table. Dumping strings from the modules
shows the Huawei entries:

```
HUAWEI  HVY-WXX9    M1060 / M1040 / M1020 / M1010
HUAWEI  BOM-WXX9    M1010
HUAWEI  KLVL-WXX9   M1010
HUAWEI  KLVL-WXXW   M1010
Valve, Google, ASUS, Lenovo …
```

Our board reports `board_vendor = HUAWEI`, `product_name = NBM-WXX9`,
`board_name = NBM-WXX9-PCB-B2`, `board_version = product_version = M1020`. No entry
matches, and `snd_acp_pci`'s probe bails out early — *without a single log line* —
with `-ENODEV` (`tee` into the driver's bind file reports `No such device`).

Two more details worth knowing:

1. **DMI prefix matching**: the table entries are matched as prefixes of
   `/sys/class/dmi/id/board_name`, which is why entries say bare `HVY-WXX9` while real
   boards report e.g. `NBM-WXX9-PCB-B2`. That means an 8-character replacement string
   is all we need.
2. **Signature enforcement**: Ubuntu kernels are built with module signature
   enforcement (`modprobe: ERROR: could not insert …: Key was rejected by service`),
   so a patched module must be signed with a key the kernel trusts — hence the MOK key
   dance below.

## The fix, step by step

### 1. Byte-patch the DMI tables (`bytepatch.py`)

All substitutions keep the original byte length so the adjacent `struct dmi_strmatch`
fields are untouched:

| Module | Original | Replacement | Purpose |
|---|---|---|---|
| `snd-acp-config.ko` | `HVY-WXX9` (×4) | `NBM-WXX9` | lets `snd_amd_acp_find_config()` accept the board and expose the ACP platform devices |
| `snd-acp-legacy-mach.ko` | `HVY-WXX9` (×4) | `NBM-WXX9` | lets the machine driver pass its own DMI check |
| `snd-acp-legacy-mach.ko` | `BOM-WXX9` (×1) | `NBM-WXX9` | selects the machine config entry that actually works (see below) |
| `snd-acp-legacy-mach.ko` | version slot `M1010` (at `+0x50` from that entry's name) | `M1020` | the entry's DMI version field must equal this board's version (`M1020`) |

`dmi_first_match()` scans the table in order and returns the **first** entry that
matches; `BOM-WXX9` sits before the four `HVY-WXX9` entries, so renaming `BOM-WXX9`
makes it the winner.

### 2. Why `BOM-WXX9` and not `HVY-WXX9`

This was determined empirically:

- Matching the `HVY-WXX9 / M1020` entry produced a working card, but the speakers
  played **continuously corrupted audio** (loud static/rasp regardless of played
  content) — the codecs were mis-clocked.
- Re-pointing the table to the `BOM-WXX9` entry made playback clean on the
  first try.

The entries apparently configure different MCLK/frequency schemes in
`snd-acp-legacy-mach`; the `BOM-WXX9` one happens to match this hardware. If you are
adapting this patch to a different sub-board or version, the same experiment applies:
try the neighbouring entries (`KLVL-WXX[W9]`, `HVY-WXX9 M1010/M1040/M1060`) by moving
the rewritten strings, and listen.

### 3. Signing your modules (MOK)

```bash
mkdir -p mok
openssl req -x509 -nodes -new -config mok/mok.cnf \
            -keyout mok/mok.priv -out mok/mok.crt -days 36500
openssl x509 -in mok/mok.crt -outform DER -out mok/mok.der

sudo mokutil --import mok/mok.der     # set an enrollment password
sudo reboot
# at the blue MokManager screen: Enroll MOK -> Continue -> Yes -> password -> Reboot
```

After enrollment, `modprobe` accepts modules signed with this key. Secure Boot being
off is irrelevant — the MOK chain is consulted by module verification either way.

Signing mechanics: the on-disk `.ko.zst` files are compressed. The signature must live
*inside the decompressed image*, so the script decompresses → patches → signs
(`sign-file` from `linux-headers`) → recompresses.

### 4. Persistence across kernel updates

`/etc/kernel/postinst.d/zz-huawei-audio-patch` invokes the patch script with the new
kernel release after every `linux-image` install, so patched modules survive
`apt upgrade` instead of being replaced back by the package.

## Files

| File | Purpose |
|---|---|
| `bytepatch.py` | The patch definitions; pure string substitutions on decompressed module images |
| `huawei-audio-patch.sh` | Driver script: locate modules, keep `.orig` backups, patch, sign, recompress for every installed kernel |
| `mok/mok.cnf` | OpenSSL config template for generating your own MOK key |
| `mok/*` (other) | **Not committed** — your private key, certificate and working module images |

## Rollback

```bash
sudo mv /usr/lib/modules/$(uname -r)/kernel/sound/soc/amd/snd-acp-config.ko.zst.orig \
        /usr/lib/modules/$(uname -r)/kernel/sound/soc/amd/snd-acp-config.ko.zst
sudo mv /usr/lib/modules/$(uname -r)/kernel/sound/soc/amd/acp/snd-acp-legacy-mach.ko.zst.orig \
        /usr/lib/modules/$(uname -r)/kernel/sound/soc/amd/acp/snd-acp-legacy-mach.ko.zst
sudo rm -f /etc/kernel/postinst.d/zz-huawei-audio-patch /usr/local/sbin/huawei-audio-patch
sudo reboot
```

## Notes and gotchas

- **Adapting to another sub-board**: check your own identity with
  `cat /sys/class/dmi/id/{board_vendor,board_name,board_version,product_name,product_version}`.
  Then adjust the target string and the version slot (`NBM_VERSION=...` env var in
  `bytepatch.py`), and if audio is corrupted, experiment with matching the neighbouring
  DMI entries as described above.
- The version-slot patch assumes the upstream table layout (Ubuntu kernels); the
  `+0x50` offset can be verified with `strings -t x` on the decompressed module.
- `.orig` files are created next to the patched modules and are the revert path.
- Never commit `mok/mok.priv` / `mok.der` / `mok.crt` — they are git-ignored, and the
  repo intentionally contains only the `mok.cnf` template so every user generates their
  own key.

## Debugging story (for people whose board is also missing)

The full discovery path, useful if your DMI string differs:

1. `cat /proc/asound/cards` — what the kernel already exposes.
2. `lspci -d 1022:` — find the ACP function (`03:00.5`).
3. Check which driver owns it: `readlink /sys/bus/pci/devices/0000:03:00.5/driver`.
4. `sudo dmesg | grep -iE "acp|codec"` — silence from the new driver = silent `-ENODEV`.
5. `strings /usr/lib/modules/$(uname -r)/.../snd-acp-config.ko.zst | grep -E "WXX9|HUAWEI"`
   — your board name almost certainly absent (and same for `snd-acp-legacy-mach`).
6. Patch + sign the modules (this repo), reboot, and confirm the card appears:
   `aplay -l` shows a second card (`acp3x-es83xx` here), then
   `dmesg` gains `acp_mach acp3x-es83xx: successfully probed the sound card`.
7. If playback is corrupted rather than silent, the DMI entry you matched carries a
   wrong MCLK configuration — try the other Huawei entries.
