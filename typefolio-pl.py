#!/usr/bin/env python3
"""AltGr national layouts for the reMarkable Type Folio (US hardware).

Stock xochitl compiles its keyboard layouts into the Qt platform plugin
/usr/lib/plugins/platforms/libepaper.so and offers no way to add one. This
tool binary-patches the compiled-in US keymap so that AltGr (right Alt)
produces national characters, in the spirit of the "Polish programmers"
layout. Several layouts are available (see `layouts` command); each mirrors
the letter assignments of the corresponding xkeyboard-config layout.

It is build-agnostic: the keymap array is located via the dynamic symbol
  EpaperEvdevKeyboardMap::Locale::UnitedStates::keymap
and the 16-byte entry format is verified against known entries before any
byte is modified. New entries overwrite mappings for keys that do not
physically exist on the Type Folio (numeric keypad, NumLock, ScrollLock,
function keys); colliding AltGr mappings are replaced in place.

Usage:
    typefolio-pl.py layouts                       # list available layouts
    typefolio-pl.py setup                         # install an SSH key (asks password once)
    typefolio-pl.py check                         # report device/patch status
    typefolio-pl.py patch [--layout pl] [--dry-run]
    typefolio-pl.py restore                       # restore the original plugin
    Options: --host root@10.11.99.1  --ask-pass  --no-restart

Authentication: with SSH keys installed (see `setup`) everything just works;
otherwise pass --ask-pass to type the tablet's root password (shown on the
device under Settings -> General -> Help -> Copyright and licenses). The
TYPEFOLIO_PASSWORD environment variable is honored with --ask-pass.

Requires: python3 and OpenSSH (ssh/scp) on this machine.
Tested on reMarkable 2, OS 3.26. After every OS update, re-run `patch`.
"""

import argparse
import atexit
import getpass
import hashlib
import os
import stat
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

# Keys absent on the Type Folio whose keymap entries are safe to sacrifice:
# numeric keypad, NumLock, ScrollLock, KPENTER, KPSLASH, and F1-F12.
SACRIFICIAL = ({55, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83,
                96, 98} | set(range(59, 69)) | {87, 88})

# US key -> Linux keycode, and the Qt keycode of that key's base US character.
KEYS = {}
for i, ch in enumerate("1234567890-="):
    KEYS[ch] = (2 + i, ord("1234567890-="[i].upper() if ch.isalpha() else ch))
for i, ch in enumerate("qwertyuiop[]"):
    KEYS[ch] = (16 + i, ord(ch.upper()))
for i, ch in enumerate("asdfghjkl;'"):
    KEYS[ch] = (30 + i, ord(ch.upper()))
for i, ch in enumerate("zxcvbnm,./"):
    KEYS[ch] = (44 + i, ord(ch.upper()))
KEYS["\\"] = (43, ord("\\"))
KEYS["`"] = (41, ord("`"))

# Layout tables: key -> (AltGr char, AltGr+Shift char or None).
# Sources: xkeyboard-config symbols files (letter assignments only; the
# source layouts' punctuation extras and dead keys are intentionally omitted).
LAYOUTS = {
    "pl": ("Polish (programmers)", {
        "a": "ąĄ", "c": "ćĆ", "e": "ęĘ", "l": "łŁ", "n": "ńŃ",
        "o": "óÓ", "s": "śŚ", "x": "źŹ", "z": "żŻ",
    }),
    "ro": ("Romanian (programmers)", {
        "q": "âÂ", "w": "ßẞ", "t": "țȚ", "i": "îÎ",
        "a": "ăĂ", "s": "șȘ", "d": "đĐ", "l": "łŁ",
    }),
    "lv": ("Latvian", {
        "e": "ēĒ", "r": "ŗŖ", "u": "ūŪ", "i": "īĪ", "o": "ōŌ",
        "a": "āĀ", "s": "šŠ", "g": "ģĢ", "k": "ķĶ", "l": "ļĻ",
        "z": "žŽ", "c": "čČ", "n": "ņŅ",
    }),
    "lt": ("Lithuanian (US)", {
        "1": "ąĄ", "2": "čČ", "3": "ęĘ", "4": "ėĖ", "5": "įĮ",
        "6": "šŠ", "7": "ųŲ", "8": "ūŪ", "=": "žŽ",
    }),
    "hr": ("Croatian (US)", {
        "y": "zZ", "z": "yY", "[": "šŠ", "]": "đĐ", "k": "ł",
        "l": "Ł", ";": "čČ", "'": "ćĆ", "\\": "žŽ",
    }),
    "szl": ("Silesian", {
        "e": "ãÃ", "o": "ôÔ", "u": "ōŌ", "p": "õÕ", "a": "ŏŎ",
        "s": "śŚ", "z": "żŻ", "x": "źŹ", "c": "ćĆ", "n": "ńŃ",
    }),
    "csb": ("Kashubian", {
        "e": "éÉ", "r": "ëË", "u": "ùÙ", "i": "òÒ", "o": "óÓ",
        "p": "ôÔ", "a": "ąĄ", "s": "ãÃ", "l": "łŁ", "z": "żŻ",
        "n": "ńŃ",
    }),
    "en-intl": ("English (US intl., AltGr letters only)", {
        "q": "äÄ", "w": "åÅ", "e": "éÉ", "r": "ëË", "t": "þÞ",
        "y": "üÜ", "u": "úÚ", "i": "íÍ", "o": "óÓ", "p": "öÖ",
        "a": "áÁ", "s": "ß", "d": "ðÐ", "j": "ïÏ", "k": "œŒ",
        "l": "øØ", "z": "æÆ", "x": "œŒ", "n": "ñÑ", ",": "çÇ",
    }),
}


def layout_entries(name):
    """Yield (keycode, required_mods, unicode, qtcode) for a layout."""
    _, table = LAYOUTS[name]
    for key, chars in table.items():
        kc, qt = KEYS[key]
        yield kc, MOD_ALTGR, ord(chars[0]), qt
        if len(chars) > 1:
            yield kc, MOD_ALTGR | MOD_SHIFT, ord(chars[1]), qt


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
    altgr_ok = False
    slots = []
    existing = {}  # (keycode, mods) -> entry position
    for pos, (kc, uni, qt, mods, _, flags, special) in iter_entries(data, off, size):
        if kc == KEY_RIGHTALT and flags == FLAG_MODIFIER and special == MOD_ALTGR:
            altgr_ok = True
        if kc in SACRIFICIAL and flags == 0:
            slots.append(pos)
        elif mods & MOD_ALTGR:
            existing[(kc, mods)] = (pos, uni)
    matches = {}
    for name in LAYOUTS:
        want = list(layout_entries(name))
        got = sum(1 for kc, m, uni, _ in want
                  if existing.get((kc, m), (None, None))[1] == uni)
        matches[name] = (got, len(want))
    return {
        "offset": off, "size": size, "entries": size // ENTRY_SIZE,
        "altgr_wired": altgr_ok, "slots": slots, "existing": existing,
        "matches": matches,
    }


def detect_layout(info):
    """Return (name, got, want) of the best-matching layout, or None."""
    best = max(info["matches"].items(), key=lambda kv: kv[1][0] / kv[1][1])
    name, (got, want) = best
    return (name, got, want) if got else None


def apply_patch(data, layout):
    """Return (patched_bytes, message). Raises SystemExit on any safety failure."""
    data = bytearray(data)
    info = analyze(data)
    want = list(layout_entries(layout))
    got, total = info["matches"][layout]
    if got == total:
        return bytes(data), "already patched"
    detected = detect_layout(info)
    if detected and detected[0] != layout:
        raise SystemExit(
            f"error: device already carries '{detected[0]}' entries "
            f"({detected[1]}/{detected[2]}) — run `restore` first, then patch"
        )
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
    # Colliding AltGr mappings are replaced in place; the rest go into slots.
    slots = list(info["slots"])
    needed = sum(1 for kc, m, _, _ in want if (kc, m) not in info["existing"])
    if len(slots) < needed:
        raise SystemExit(
            f"error: only {len(slots)} sacrificial slots for {needed} new entries"
        )
    for kc, mods, uni, qt in want:
        if (kc, mods) in info["existing"]:
            pos = info["existing"][(kc, mods)][0]
        else:
            pos = slots.pop(0)
        data[pos : pos + ENTRY_SIZE] = ENTRY.pack(kc, uni, qt, mods, 0, FLAG_LETTER, 0)
    got, total = analyze(data)["matches"][layout]
    if got != total:
        raise SystemExit("error: post-patch verification failed")
    return bytes(data), f"patched {total} entries ({LAYOUTS[layout][0]})"


def sha8(b):
    return hashlib.sha256(b).hexdigest()[:8]


SSH_ENV = None  # set up by use_password(); None means key-based auth
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]


def use_password():
    """Route the root password to ssh/scp via SSH_ASKPASS (no sshpass needed)."""
    global SSH_ENV, SSH_OPTS
    pw = os.environ.get("TYPEFOLIO_PASSWORD") or getpass.getpass(
        "reMarkable root password (Settings > General > Help > Copyright and licenses): ")
    fd, askpass = tempfile.mkstemp(prefix="typefolio-askpass-")
    os.write(fd, b'#!/bin/sh\nprintf "%s" "$TYPEFOLIO_PW"\n')
    os.close(fd)
    os.chmod(askpass, stat.S_IRWXU)
    atexit.register(os.unlink, askpass)
    SSH_ENV = {**os.environ, "TYPEFOLIO_PW": pw, "SSH_ASKPASS": askpass,
               "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": os.environ.get("DISPLAY", ":0")}
    SSH_OPTS = ["-o", "NumberOfPasswordPrompts=1",
                "-o", "StrictHostKeyChecking=accept-new"]


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, env=SSH_ENV, **kw)


def ssh(host, script, capture=False):
    return run(["ssh", *SSH_OPTS, host, script], capture_output=capture, text=capture)


def fetch_plugin(host, tmpdir):
    local = Path(tmpdir) / "libepaper.so"
    run(["scp", "-q", *SSH_OPTS, f"{host}:{PLUGIN}", str(local)])
    return local.read_bytes()


def cmd_layouts(_args):
    for name, (label, table) in LAYOUTS.items():
        chars = " ".join(chars[0] for chars in table.values())
        print(f"{name:8s} {label:40s} {chars}")


def cmd_check(args):
    with tempfile.TemporaryDirectory() as tmp:
        data = fetch_plugin(args.host, tmp)
    info = analyze(data)
    ver = ssh(args.host,
              "sed -n 's/^REMARKABLE_RELEASE_VERSION=//p' /usr/share/remarkable/update.conf",
              capture=True).stdout.strip()
    detected = detect_layout(info)
    if detected is None:
        state = "not patched"
    else:
        name, got, total = detected
        full = "fully" if got == total else f"PARTIALLY ({got}/{total})"
        state = f"{full} patched: {name} — {LAYOUTS[name][0]}"
    print(f"OS version:      {ver or 'unknown'}")
    print(f"plugin sha256:   {sha8(data)}…")
    print(f"US keymap:       {info['entries']} entries at offset {info['offset']:#x}")
    print(f"AltGr modifier:  {'wired' if info['altgr_wired'] else 'NOT wired'}")
    print(f"Layout:          {state}")


def cmd_patch(args):
    with tempfile.TemporaryDirectory() as tmp:
        orig = fetch_plugin(args.host, tmp)
        patched, msg = apply_patch(orig, args.layout)
        if msg == "already patched":
            print(f"Device already has the '{args.layout}' layout — nothing to do.")
            return
        print(f"{msg} (sha {sha8(orig)}… -> {sha8(patched)}…)")
        if args.dry_run:
            print("Dry run — device not modified.")
            return
        backup = f"{BACKUP_DIR}/libepaper.so.orig-{sha8(orig)}"
        local = Path(tmp) / "libepaper-patched.so"
        local.write_bytes(patched)
        ssh(args.host, f"cp -n {PLUGIN} {backup}")
        run(["scp", "-q", *SSH_OPTS, str(local), f"{args.host}:/tmp/libepaper-patched.so"])
        ssh(args.host, f"mv /tmp/libepaper-patched.so {PLUGIN}")
        print(f"Deployed. Original kept at {backup}")
        restart(args)


def cmd_setup(args):
    """Install an SSH public key on the tablet so no password is needed later."""
    ssh_dir = Path.home() / ".ssh"
    pub = next((p for p in (ssh_dir / f"id_{t}.pub" for t in ("ed25519", "ecdsa", "rsa"))
                if p.exists()), None)
    if pub is None:
        key = ssh_dir / "id_ed25519"
        print(f"No SSH key found — generating {key}")
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
        pub = key.with_suffix(".pub")
    pubkey = pub.read_text().strip()
    print(f"Installing {pub} on {args.host} …")
    ssh(args.host,
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qF '{pubkey}' ~/.ssh/authorized_keys 2>/dev/null || "
        f"printf '%s\\n' '{pubkey}' >> ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys")
    # Verify that key-based auth now works.
    global SSH_ENV, SSH_OPTS
    SSH_ENV = None
    SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
    ssh(args.host, "true")
    print("Key installed — future runs won't need the password.")


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
    p.add_argument("command", choices=["layouts", "setup", "check", "patch", "restore"])
    p.add_argument("--layout", default="pl", choices=sorted(LAYOUTS),
                   help="layout to apply with `patch` (default: %(default)s)")
    p.add_argument("--host", default="root@10.11.99.1",
                   help="SSH destination of the tablet (default: %(default)s)")
    p.add_argument("--ask-pass", action="store_true",
                   help="authenticate with the tablet's root password instead of SSH keys")
    p.add_argument("--dry-run", action="store_true", help="patch: don't modify the device")
    p.add_argument("--no-restart", action="store_true", help="don't restart xochitl")
    args = p.parse_args()
    if args.ask_pass or args.command == "setup":
        use_password()
    try:
        {"layouts": cmd_layouts, "setup": cmd_setup, "check": cmd_check,
         "patch": cmd_patch, "restore": cmd_restore}[args.command](args)
    except subprocess.CalledProcessError as e:
        hint = ("" if args.ask_pass or args.command == "setup" else
                "\nhint: if SSH keys aren't set up on the tablet, retry with "
                "--ask-pass, or run `setup` once")
        raise SystemExit(f"error: command failed: {' '.join(e.cmd)}{hint}")


if __name__ == "__main__":
    main()
