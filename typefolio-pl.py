#!/usr/bin/env python3
"""Polish Programmers layout (AltGr diacritics) for the reMarkable Type Folio.

Stock xochitl compiles its keyboard layouts into the Qt platform plugin
/usr/lib/plugins/platforms/libepaper.so and offers no Polish layout. This tool
binary-patches the compiled-in US keymap so that AltGr (right Alt) produces
Polish diacritics (a la the standard "Polish programmers" layout):

    AltGr+a c e l n o s x z        ->  ą ć ę ł ń ó ś ź ż
    AltGr+Shift+a c e l n o s x z  ->  Ą Ć Ę Ł Ń Ó Ś Ź Ż

It is build-agnostic: the keymap array is located via the dynamic symbol
  EpaperEvdevKeyboardMap::Locale::UnitedStates::keymap
and the 16-byte entry format is verified against known entries before any
byte is modified. The 18 new entries overwrite mappings for keys that do not
physically exist on the Type Folio (numeric keypad, NumLock, ScrollLock).

Usage:
    typefolio-pl.py check                 # report device/patch status
    typefolio-pl.py patch [--dry-run]     # apply the patch (idempotent)
    typefolio-pl.py restore               # restore the original plugin
    Options: --host root@10.11.99.1  --no-restart

Requires: python3 on this machine, ssh/scp access to the tablet.
Tested on reMarkable 2, OS 3.26. After every OS update, re-run `patch`.
"""

import argparse
import hashlib
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = "/usr/lib/plugins/platforms/libepaper.so"
BACKUP_DIR = "/home/root"
SYMBOL = b"_ZN22EpaperEvdevKeyboardMap6Locale12UnitedStates6keymapE"

ENTRY = struct.Struct("<HHIBBHI")  # keycode, unicode, qtcode, mods, pad, flags, special
ENTRY_SIZE = ENTRY.size  # 16

FLAG_DEAD, FLAG_LETTER, FLAG_MODIFIER, FLAG_SYSTEM = 1, 2, 4, 8
MOD_SHIFT, MOD_ALTGR = 0x01, 0x02
QT_KEY_ALTGR = 0x01001103
KEY_RIGHTALT = 100

# Linux keycodes of keys absent on the Type Folio, safe to sacrifice:
# KPASTERISK, NUMLOCK, SCROLLLOCK, KP7..KPDOT, KPENTER, KPSLASH
SACRIFICIAL = {55, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 96, 98}

# (keycode, lowercase, uppercase, base latin letter)
POLISH = [
    (30, 0x0105, 0x0104, "A"),  # ą Ą
    (46, 0x0107, 0x0106, "C"),  # ć Ć
    (18, 0x0119, 0x0118, "E"),  # ę Ę
    (38, 0x0142, 0x0141, "L"),  # ł Ł
    (49, 0x0144, 0x0143, "N"),  # ń Ń
    (24, 0x00F3, 0x00D3, "O"),  # ó Ó
    (31, 0x015B, 0x015A, "S"),  # ś Ś
    (45, 0x017A, 0x0179, "X"),  # ź Ź
    (44, 0x017C, 0x017B, "Z"),  # ż Ż
]


def polish_entries():
    out = []
    for kc, lo, up, base in POLISH:
        qt = ord(base)
        out.append(ENTRY.pack(kc, lo, qt, MOD_ALTGR, 0, FLAG_LETTER, 0))
        out.append(ENTRY.pack(kc, up, qt, MOD_ALTGR | MOD_SHIFT, 0, FLAG_LETTER, 0))
    return out


class Elf:
    """Minimal read-only ELF parser (32/64-bit little-endian)."""

    def __init__(self, data):
        if data[:4] != b"\x7fELF" or data[5] != 1:
            raise SystemExit("error: not a little-endian ELF file")
        self.data = data
        self.is64 = data[4] == 2
        if self.is64:
            e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
            shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
            self.sections = [
                struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + i * shentsize)
                for i in range(shnum)
            ]
        else:
            e_shoff = struct.unpack_from("<I", data, 0x20)[0]
            shentsize, shnum = struct.unpack_from("<HH", data, 0x2E)
            self.sections = [
                struct.unpack_from("<10I", data, e_shoff + i * shentsize)
                for i in range(shnum)
            ]
        # section tuple: name, type, flags, addr, offset, size, link, info, align, entsize
        self.dynsym = self._section_by_type(11)  # SHT_DYNSYM
        if not self.dynsym:
            raise SystemExit("error: no .dynsym section")
        self.dynstr = self.sections[self.dynsym[6]]

    def _section_by_type(self, t):
        return next((s for s in self.sections if s[1] == t), None)

    def find_symbol(self, name):
        """Return (vaddr, size) of a dynamic symbol."""
        off, size = self.dynsym[4], self.dynsym[5]
        str_off = self.dynstr[4]
        if self.is64:
            entsize, fmt = 24, "<IBBHQQ"  # name, info, other, shndx, value, size
        else:
            entsize, fmt = 16, "<IIIBBH"  # name, value, size, info, other, shndx
        for pos in range(off, off + size, entsize):
            if self.is64:
                nm, _, _, _, value, sz = struct.unpack_from(fmt, self.data, pos)
            else:
                nm, value, sz, _, _, _ = struct.unpack_from(fmt, self.data, pos)
            end = self.data.index(b"\0", str_off + nm)
            if self.data[str_off + nm : end] == name:
                return value, sz
        return None, None

    def vaddr_to_offset(self, vaddr):
        for s in self.sections:
            addr, off, size = s[3], s[4], s[5]
            if s[1] != 8 and addr <= vaddr < addr + size:  # skip SHT_NOBITS
                return vaddr - addr + off
        raise SystemExit(f"error: vaddr {vaddr:#x} not in any section")


def locate_keymap(data):
    elf = Elf(data)
    vaddr, size = elf.find_symbol(SYMBOL)
    if vaddr is None:
        raise SystemExit(
            "error: US keymap symbol not found — plugin layout changed, "
            "this tool needs updating for your OS version"
        )
    if size % ENTRY_SIZE:
        raise SystemExit(f"error: keymap size {size:#x} not a multiple of {ENTRY_SIZE}")
    off = elf.vaddr_to_offset(vaddr)
    # Format sanity check: plain 'q' entry must exist as-is in the table.
    q_entry = ENTRY.pack(16, ord("q"), ord("Q"), 0, 0, FLAG_LETTER, 0)
    if q_entry not in data[off : off + size]:
        raise SystemExit(
            "error: keymap entry format check failed — struct layout changed, "
            "refusing to patch"
        )
    return off, size


def iter_entries(data, off, size):
    for pos in range(off, off + size, ENTRY_SIZE):
        yield pos, ENTRY.unpack_from(data, pos)


def analyze(data):
    off, size = locate_keymap(data)
    wanted = {(kc, m): uni for kc, lo, up, _ in POLISH
              for m, uni in ((MOD_ALTGR, lo), (MOD_ALTGR | MOD_SHIFT, up))}
    present = 0
    altgr_ok = False
    slots = []
    for pos, (kc, uni, qt, mods, _, flags, special) in iter_entries(data, off, size):
        if wanted.get((kc, mods)) == uni:
            present += 1
        if kc == KEY_RIGHTALT and flags == FLAG_MODIFIER and special == MOD_ALTGR:
            altgr_ok = True
        if kc in SACRIFICIAL and flags == 0:
            slots.append(pos)
    return {
        "offset": off, "size": size, "entries": size // ENTRY_SIZE,
        "polish_present": present, "polish_total": len(wanted),
        "altgr_wired": altgr_ok, "slots": slots,
    }


def apply_patch(data):
    """Return (patched_bytes, message). Raises SystemExit on any safety failure."""
    data = bytearray(data)
    info = analyze(data)
    new = polish_entries()
    if info["polish_present"] == len(new):
        return bytes(data), "already patched"
    if not info["altgr_wired"]:
        # Right Alt not wired as AltGr in this build: rewrite its entry.
        fixed = False
        for pos, (kc, *_rest) in iter_entries(data, info["offset"], info["size"]):
            if kc == KEY_RIGHTALT:
                data[pos : pos + ENTRY_SIZE] = ENTRY.pack(
                    KEY_RIGHTALT, 0xFFFF, QT_KEY_ALTGR, 0, 0, FLAG_MODIFIER, MOD_ALTGR)
                fixed = True
                break
        if not fixed:
            raise SystemExit("error: no KEY_RIGHTALT entry found to wire as AltGr")
    if len(info["slots"]) < len(new):
        raise SystemExit(
            f"error: only {len(info['slots'])} sacrificial slots for {len(new)} entries"
        )
    for entry, pos in zip(new, info["slots"]):
        data[pos : pos + ENTRY_SIZE] = entry
    # Verify.
    if analyze(data)["polish_present"] != len(new):
        raise SystemExit("error: post-patch verification failed")
    return bytes(data), f"patched {len(new)} entries"


def sha8(b):
    return hashlib.sha256(b).hexdigest()[:8]


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def ssh(host, script, capture=False):
    return run(["ssh", "-o", "BatchMode=yes", host, script],
               capture_output=capture, text=capture)


def fetch_plugin(host, tmpdir):
    local = Path(tmpdir) / "libepaper.so"
    run(["scp", "-q", f"{host}:{PLUGIN}", str(local)])
    return local.read_bytes()


def cmd_check(args):
    with tempfile.TemporaryDirectory() as tmp:
        data = fetch_plugin(args.host, tmp)
    info = analyze(data)
    ver = ssh(args.host,
              "sed -n 's/^REMARKABLE_RELEASE_VERSION=//p' /usr/share/remarkable/update.conf",
              capture=True).stdout.strip()
    state = ("fully patched" if info["polish_present"] == info["polish_total"]
             else "NOT patched" if info["polish_present"] == 0
             else f"PARTIALLY patched ({info['polish_present']}/{info['polish_total']})")
    print(f"OS version:      {ver or 'unknown'}")
    print(f"plugin sha256:   {sha8(data)}…")
    print(f"US keymap:       {info['entries']} entries at offset {info['offset']:#x}")
    print(f"AltGr modifier:  {'wired' if info['altgr_wired'] else 'NOT wired'}")
    print(f"Polish entries:  {state}")


def cmd_patch(args):
    with tempfile.TemporaryDirectory() as tmp:
        orig = fetch_plugin(args.host, tmp)
        patched, msg = apply_patch(orig)
        if msg == "already patched":
            print("Device is already patched — nothing to do.")
            return
        print(f"{msg} (sha {sha8(orig)}… -> {sha8(patched)}…)")
        if args.dry_run:
            print("Dry run — device not modified.")
            return
        backup = f"{BACKUP_DIR}/libepaper.so.orig-{sha8(orig)}"
        local = Path(tmp) / "libepaper-pl.so"
        local.write_bytes(patched)
        ssh(args.host, f"cp -n {PLUGIN} {backup}")
        run(["scp", "-q", str(local), f"{args.host}:/tmp/libepaper-pl.so"])
        ssh(args.host, f"mv /tmp/libepaper-pl.so {PLUGIN}")
        print(f"Deployed. Original kept at {backup}")
        restart(args)


def cmd_restore(args):
    out = ssh(args.host, f"ls -t {BACKUP_DIR}/libepaper.so.orig-* 2>/dev/null | head -n 1",
              capture=True).stdout.strip()
    if not out:
        raise SystemExit(f"error: no backup found in {BACKUP_DIR} on the device")
    ssh(args.host, f"cp {out} {PLUGIN}")
    print(f"Restored {out} -> {PLUGIN}")
    restart(args)


def restart(args):
    if args.no_restart:
        print("Skipped xochitl restart (--no-restart). Restart it to take effect.")
        return
    ssh(args.host, "systemctl restart xochitl && sleep 2 && systemctl is-active --quiet xochitl")
    print("xochitl restarted and running.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["check", "patch", "restore"])
    p.add_argument("--host", default="root@10.11.99.1",
                   help="SSH destination of the tablet (default: %(default)s)")
    p.add_argument("--dry-run", action="store_true", help="patch: don't modify the device")
    p.add_argument("--no-restart", action="store_true", help="don't restart xochitl")
    args = p.parse_args()
    try:
        {"check": cmd_check, "patch": cmd_patch, "restore": cmd_restore}[args.command](args)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"error: command failed: {' '.join(e.cmd)}")


if __name__ == "__main__":
    main()
