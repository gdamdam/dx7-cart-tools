#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Send a DX7-format SysEx cart to a Korg volca fm2 (or any DX7-compatible synth).

Why this exists: the volca fm2 gives NO indication that it received a cart, and
it doesn't reload the patch you're currently sitting on. A successful transfer
and a completely failed one look and sound identical until you change program.
That makes GUI librarian apps hard to trust -- you can't tell whether the app
sent nothing or the synth silently accepted everything.

So this script validates the file first and tells you exactly what it sent. If
it reports "sent" after passing validation, the bytes really did leave the
interface, and anything still wrong is downstream (cable, jack, receive channel).

Usage
    python3 dx7cart.py --list                      # show MIDI ports
    python3 dx7cart.py --test-notes                # prove the MIDI path first
    python3 dx7cart.py --info    --file bank.syx   # validate + list patch names
    python3 dx7cart.py --dry-run --file bank.syx   # full check, send nothing
    python3 dx7cart.py           --file bank.syx   # send it
    python3 dx7cart.py --file bank.syx --port "UM-ONE" --yes
    python3 -m unittest test_dx7cart               # run the tests

Requires: mido and python-rtmidi  (pip install mido python-rtmidi)
"""

import argparse
import sys
from pathlib import Path

__version__ = "1.0.0"

# Defaults -- edit these to run with fewer arguments. Set DEFAULT_CART to a Path
# if you usually send the same file and want to run the script bare. With
# DEFAULT_PORT = None the only MIDI output is picked automatically; with several,
# --port is required so a cart never goes to a guessed destination.
DEFAULT_CART = None
DEFAULT_PORT = None
DEFAULT_CHANNEL = 1          # MIDI channel the synth listens on, 1-16

YAMAHA_ID = 0x43
FMT_1VOICE, FMT_32VOICE = 0x00, 0x09
# Payload size each DX7 dump format must carry. Checking the declared size
# alone is not enough: a header that declares 0 bytes would match an empty body.
EXPECTED_SIZE = {FMT_1VOICE: 155, FMT_32VOICE: 4096}


def split_messages(raw: bytes):
    """Split a .syx file into individual F0...F7 messages.

    Cart files usually hold exactly one message, but some packs ship several
    (multiple banks, or one message per patch), so never assume a single blob.
    Bytes outside F0...F7 are not silently dropped: a missing F7 would otherwise
    merge two messages, and stray bytes usually mean a corrupt download.
    """
    msgs, i = [], 0
    while True:
        start = raw.find(b"\xF0", i)
        if start < 0:
            if raw[i:].strip(b"\x00"):
                raise ValueError(f"{len(raw) - i} stray bytes after the last "
                                 f"SysEx message (byte {i})")
            break
        if raw[i:start].strip(b"\x00"):
            raise ValueError(f"{start - i} stray bytes before the SysEx "
                             f"message at byte {start}")
        end = raw.find(b"\xF7", start)
        if end < 0:
            raise ValueError(f"unterminated SysEx starting at byte {start}")
        nested = raw.find(b"\xF0", start + 1, end)
        if nested >= 0:
            raise ValueError(f"SysEx at byte {start} has no F7 before the next "
                             f"F0 at byte {nested}")
        msgs.append(raw[start:end + 1])
        i = end + 1
    if not msgs:
        raise ValueError("no SysEx messages found (no F0 byte in file)")
    return msgs


def describe(msg: bytes):
    """Identify a message and verify its checksum where the format defines one.

    Returns (label, list_of_problems). A DX7 bulk dump is
    F0 43 0n <fmt> <size hi> <size lo> <data...> <checksum> F7, and the
    checksum is the 7-bit two's complement of the data sum.
    """
    problems = []
    if len(msg) < 3 or msg[1] != YAMAHA_ID:
        return f"non-Yamaha SysEx ({len(msg)} bytes)", problems
    # Only bulk dumps (sub-status 0) carry a size and checksum. Parameter
    # changes (1n) and dump requests (2n) are short and have neither.
    if msg[2] & 0xF0:
        return f"Yamaha SysEx, sub-status {msg[2] >> 4}, {len(msg)} bytes", problems
    if len(msg) < 8:
        problems.append(f"truncated bulk dump ({len(msg)} bytes)")
        return f"Yamaha bulk dump, {len(msg)} bytes", problems

    fmt = msg[3]
    # Byte count is two 7-bit MIDI data bytes, so it shifts by 7, not 8:
    # a 32-voice cart declares 0x20 0x00 = (32 << 7) = 4096 bytes.
    size = (msg[4] << 7) | msg[5]
    body, stored = msg[6:-2], msg[-2]
    calc = (128 - (sum(body) & 0x7F)) & 0x7F
    if calc != stored:
        problems.append(f"checksum mismatch: stored {stored}, calculated {calc}")
    if len(body) != size:
        problems.append(f"declared {size} data bytes, found {len(body)}")
    if fmt in EXPECTED_SIZE and size != EXPECTED_SIZE[fmt]:
        problems.append(f"format {fmt} must carry {EXPECTED_SIZE[fmt]} data "
                        f"bytes, header declares {size}")

    if fmt == FMT_32VOICE:
        label = f"DX7 32-voice cart, {len(msg)} bytes"
    elif fmt == FMT_1VOICE:
        label = f"DX7 single voice, {len(msg)} bytes"
    else:
        label = f"Yamaha SysEx format {fmt}, {len(msg)} bytes"
    return label, problems


def is_dump(msg: bytes, fmt: int):
    """True for a Yamaha bulk dump of the given format."""
    return (len(msg) >= 6 and msg[1] == YAMAHA_ID and not msg[2] & 0xF0
            and msg[3] == fmt)


def patch_names(msg: bytes):
    """Patch names from a 32-voice cart: last 10 bytes of each 128-byte voice."""
    if len(msg) < 6 + 4096 or not is_dump(msg, FMT_32VOICE):
        return []
    body = msg[6:6 + 4096]
    return [body[i * 128 + 118:i * 128 + 128].decode("ascii", "replace").strip()
            for i in range(32)]


def resolve_port(requested, outputs):
    """Exact match first, then unique case-insensitive substring match.

    With no port requested, a lone output is unambiguous and used directly.
    """
    if requested is None:
        if len(outputs) == 1:
            return outputs[0]
        if not outputs:
            raise SystemExit("No MIDI outputs found. Is the interface connected?")
        raise SystemExit(f"Several MIDI outputs, pick one with --port: {outputs}")
    if requested in outputs:
        return requested
    hits = [o for o in outputs if requested.lower() in o.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"No MIDI output matching {requested!r}.\n"
                         f"Available: {outputs or '(none)'}")
    raise SystemExit(f"{requested!r} is ambiguous, matches: {hits}")


def channel(value):
    """argparse type: a MIDI channel as humans number them, 1-16."""
    n = int(value)
    if not 1 <= n <= 16:
        raise argparse.ArgumentTypeError(f"MIDI channel must be 1-16, got {n}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Inspect, validate and send DX7 SysEx carts over MIDI.")
    ap.add_argument("--version", action="version",
                    version=f"dx7cart {__version__}")
    ap.add_argument("--file", type=Path, default=DEFAULT_CART,
                    help="the .syx to send" if DEFAULT_CART is None
                         else f"the .syx to send (default: {DEFAULT_CART.name})")
    ap.add_argument("--port", default=DEFAULT_PORT,
                    help="MIDI output, substring is enough (default: "
                         + (DEFAULT_PORT or "the only output, if there is one")
                         + ")")
    ap.add_argument("--channel", type=channel, default=DEFAULT_CHANNEL,
                    help="MIDI channel 1-16, only used by --test-notes")
    ap.add_argument("--delay", type=float, default=0.25, metavar="SEC",
                    help="pause between messages in multi-message files")
    ap.add_argument("--list", action="store_true", help="list MIDI ports and exit")
    ap.add_argument("--info", action="store_true",
                    help="validate and show patch names, send nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything except the actual send")
    ap.add_argument("--test-notes", action="store_true",
                    help="send test notes to prove the MIDI path, then exit")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="skip the overwrite confirmation")
    args = ap.parse_args(argv)

    if args.list:
        import mido
        print("MIDI OUTPUTS:")
        print("\n".join(f"  {n}" for n in mido.get_output_names()) or "  (none)")
        print("MIDI INPUTS:")
        print("\n".join(f"  {n}" for n in mido.get_input_names()) or "  (none)")
        return

    # Always worth running before blaming a cart: if the synth plays these, the
    # cable, interface, port and receive channel are all proven good, which
    # narrows any remaining fault to the SysEx itself.
    if args.test_notes:
        import time
        import mido
        port_name = resolve_port(args.port, mido.get_output_names())
        print(f"sending test notes on {port_name}, MIDI channel {args.channel}")
        with mido.open_output(port_name) as port:
            ch = args.channel - 1
            for note in (48, 55, 60, 67, 72):
                port.send(mido.Message("note_on", channel=ch, note=note, velocity=100))
                time.sleep(0.35)
                port.send(mido.Message("note_off", channel=ch, note=note, velocity=0))
            port.send(mido.Message("control_change", channel=ch, control=123, value=0))
        print("done -- if you heard five rising notes, the MIDI path is fine")
        return

    if args.file is None:
        raise SystemExit("No cart given: pass --file PATH, or set DEFAULT_CART "
                         "at the top of this script to run bare.")
    if not args.file.is_file():
        raise SystemExit(f"No such file: {args.file}")

    try:
        messages = split_messages(args.file.read_bytes())
    except ValueError as e:
        print(f"{args.file.name}: {e}")
        print("\nValidation failed.")
        return 1
    print(f"{args.file.name}: {args.file.stat().st_size} bytes, "
          f"{len(messages)} SysEx message(s)")

    fatal = False
    for n, msg in enumerate(messages, 1):
        label, problems = describe(msg)
        print(f"  [{n}] {label}")
        for p in problems:
            print(f"      WARNING: {p}")
            fatal = True
        if any(b >= 0x80 for b in msg[1:-1]):
            print("      WARNING: payload contains non-7-bit bytes")
            fatal = True

    if args.info:
        for n, msg in enumerate(messages, 1):
            names = patch_names(msg)
            if names:
                print(f"\nmessage {n} patch names:")
                for i, nm in enumerate(names, 1):
                    print(f"  {i:2d}. {nm}")
        if fatal:
            print("\nValidation failed.")
            return 1
        return

    if fatal:
        print("\nValidation failed. Refusing to send a malformed cart -- "
              "it would overwrite presets with garbage.")
        return 1

    # MIDI is touched only from here on, so --info works offline and without
    # mido installed.
    import mido
    port_name = resolve_port(args.port, mido.get_output_names())

    if args.dry_run:
        print(f"\ndry run: would send {len(messages)} message(s) to {port_name}")
        return

    # A cart dump overwrites programs 1-32 in place and cannot be undone, so
    # make the destination and the consequence explicit before committing.
    if not args.yes:
        print(f"\nAbout to send to {port_name!r}.")
        if any(is_dump(m, FMT_32VOICE) for m in messages):
            print("This OVERWRITES programs 1-32 on the synth. Programs 33-64 "
                  "are left alone (a DX7 cart only carries 32 patches).")
        elif any(is_dump(m, FMT_1VOICE) for m in messages):
            print("This replaces the synth's edit buffer (the current sound). "
                  "Stored programs are not overwritten.")
        try:
            if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                print("aborted")
                return
        except EOFError:
            raise SystemExit("aborted: no terminal to confirm on (use --yes)")
        except KeyboardInterrupt:
            raise SystemExit("\naborted")

    import time
    with mido.open_output(port_name) as port:
        for n, msg in enumerate(messages, 1):
            port.send(mido.Message("sysex", data=msg[1:-1]))
            print(f"  sent message {n}/{len(messages)} ({len(msg)} bytes)")
            if n < len(messages):
                time.sleep(args.delay)

    print("\nSent. Now CHANGE PROGRAM on the synth and step through 1-32 --\n"
          "the volca fm2 gives no confirmation and keeps the current patch in\n"
          "memory, so a successful load looks like nothing happened.")


if __name__ == "__main__":
    sys.exit(main() or 0)
