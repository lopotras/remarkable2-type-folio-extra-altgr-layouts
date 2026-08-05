# AltGr national layouts for the reMarkable Type Folio

Type national characters on a US-layout Type Folio using AltGr (right Alt) —
in stock xochitl, no Toltec or custom launcher required. Born as a "Polish
programmers" patch, it now supports every layout that is genuinely
"US QWERTY + AltGr letters" in xkeyboard-config:

| Layout | Name | AltGr characters |
|---|---|---|
| `pl` (default) | Polish (programmers) | ą ć ę ł ń ó ś ź ż |
| `ro` | Romanian (programmers) | ă â î ș ț đ ł ß |
| `lv` | Latvian | ā č ē ģ ī ķ ļ ņ ō ŗ š ū ž |
| `lt` | Lithuanian (US) | ą č ę ė į š ų ū ž (on the digit row) |
| `hr` | Croatian (US) | č ć đ š ž (on their national positions) |
| `szl` | Silesian | ã ć ń ŏ ô ō õ ś ź ż |
| `csb` | Kashubian | ą ã é ë ł ń ò ó ô ù ż |
| `en-intl` | English (US intl.), letters only | á ä å æ ç ð é ë í ï ñ ó ö ø œ ß þ ú ü |

`AltGr+letter` gives the lowercase form, `AltGr+Shift+letter` the uppercase;
Caps Lock behaves correctly. Each table mirrors the letter assignments of the
corresponding [xkeyboard-config](https://gitlab.freedesktop.org/xkeyboard-config/xkeyboard-config)
layout; dead keys and AltGr punctuation of the source layouts are
intentionally omitted (`en-intl` is the direct-letter subset of
"English (intl., with AltGr dead keys)"). Czech, Slovak, and Maltese were
evaluated and can't be supported faithfully — their standard layouts either
replace base US keys or rely on dead keys.

## Why a binary patch?

reMarkable compiles all Type Folio keyboard layouts into the closed-source Qt
platform plugin `/usr/lib/plugins/platforms/libepaper.so`. There is no Polish
(or Romanian, or Latvian…) table, no config file, and no keymap-loading
mechanism (the qmap support from upstream Qt was removed from their fork).
The kernel side needs nothing — the keyboard already sends AltGr as a proper
modifier.

This tool locates the compiled-in **US keymap** inside the plugin via its
dynamic symbol (`EpaperEvdevKeyboardMap::Locale::UnitedStates::keymap`),
verifies the entry format against known entries, and writes the new AltGr
entries over mappings for keys that don't physically exist on the Type Folio
(numeric keypad, NumLock, ScrollLock, F1–F12). Because the symbol is looked
up by name and the format is verified first, the same script keeps working
across OS builds — and refuses to touch anything it doesn't recognize.

In **Settings → Language and keyboard**, keep the Type Folio layout on
**US/English** — these layouts *are* the US layout plus AltGr, and the
patched US table is what that setting selects.

## Requirements

- reMarkable 2 with a Type Folio (US layout) and SSH access
  ([developer mode / root access](https://support.remarkable.com/s/article/Developer-mode)).
  Password-less SSH keys recommended.
- python3 + ssh/scp on your computer.
- Tested on OS 3.26 (rM2). The reMarkable Paper Pro is untested; the script's
  safety checks will abort rather than corrupt anything unrecognized.

## Usage

Connect the tablet via USB (it appears as `10.11.99.1`), then:

```bash
./typefolio-pl.py layouts              # list available layouts
./typefolio-pl.py check                # show OS version and patch status
./typefolio-pl.py patch                # apply Polish (default)
./typefolio-pl.py patch --layout lv    # or any layout from the table
./typefolio-pl.py restore              # put the original plugin back
```

Use `--host root@<ip>` for SSH over Wi-Fi, `--dry-run` to test without
touching the device. To switch between layouts, `restore` first, then `patch`
— the tool enforces this.

The original plugin is kept on the tablet as
`/home/root/libepaper.so.orig-<sha8>`.

## OS updates

A system update replaces `libepaper.so`, silently removing the patch.
Just run `./typefolio-pl.py patch` again after updating.

## Safety notes

- The patch only modifies keymap *data* (no code), only in entries for keys
  the Type Folio doesn't have, and verifies the result before deploying.
- Every run checks the entry format first and aborts on any mismatch, so a
  future incompatible OS build fails safe.
- Do **not** write to `/sys/pogo/status/lang` while experimenting — writes are
  sent to the keyboard's microcontroller and permanently reprogram the
  language stored in the folio itself.

## How it was reverse-engineered

Keymap entries are 16 bytes:
`{u16 keycode, u16 unicode, u32 qtcode, u8 required_modifiers, u8 pad, u16 flags, u32 special}`
— a widened variant of Qt's `QEvdevKeyboardMap::Mapping`. `required_modifiers`
bit 0x02 is AltGr; `flags` 2 marks letters (Caps Lock applies); modifier keys
carry their modifier bit in `special`. The US table already maps right Alt to
`Qt::Key_AltGr`, so adding AltGr letter entries is purely additive.

## Disclaimer

Not affiliated with reMarkable. You are modifying system files on your own
device at your own risk; the `restore` command and on-device backups make the
change fully reversible.
