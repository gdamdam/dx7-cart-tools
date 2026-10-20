"""Tests for the offline parts of dx7cart: parsing, validation, port picking.

Run with: python3 -m unittest test_dx7cart
"""

import argparse
import tempfile
import unittest
from pathlib import Path

import dx7cart as d


def dump(fmt, body, size=None):
    """Build a Yamaha bulk dump with a correct checksum."""
    size = len(body) if size is None else size
    cs = (128 - (sum(body) & 0x7F)) & 0x7F
    return bytes([0xF0, 0x43, 0x00, fmt, size >> 7, size & 0x7F]) + bytes(body) \
        + bytes([cs, 0xF7])


def cart():
    body = bytearray(4096)
    for i in range(32):
        body[i * 128 + 118:i * 128 + 128] = f"PATCH {i + 1:02d}  ".encode()
    return dump(d.FMT_32VOICE, body)


class SplitMessages(unittest.TestCase):
    def test_single_and_multiple(self):
        c = cart()
        self.assertEqual(d.split_messages(c), [c])
        self.assertEqual(d.split_messages(c + c), [c, c])

    def test_zero_padding_allowed(self):
        c = cart()
        self.assertEqual(d.split_messages(b"\x00" + c + b"\x00\x00"), [c])

    def test_stray_bytes_rejected(self):
        with self.assertRaisesRegex(ValueError, "stray bytes before"):
            d.split_messages(b"xx" + cart())
        with self.assertRaisesRegex(ValueError, "stray bytes after"):
            d.split_messages(cart() + b"xx")

    def test_missing_f7_not_merged(self):
        with self.assertRaisesRegex(ValueError, "no F7 before the next F0"):
            d.split_messages(b"\xF0\x43\x00\xF0\x43\xF7")

    def test_unterminated_and_empty(self):
        with self.assertRaisesRegex(ValueError, "unterminated"):
            d.split_messages(b"\xF0\x43\x00")
        with self.assertRaisesRegex(ValueError, "no SysEx"):
            d.split_messages(b"")


class Describe(unittest.TestCase):
    def test_valid_cart(self):
        self.assertEqual(d.describe(cart()), ("DX7 32-voice cart, 4104 bytes", []))

    def test_valid_single_voice(self):
        label, problems = d.describe(dump(d.FMT_1VOICE, [0] * 155))
        self.assertIn("single voice", label)
        self.assertEqual(problems, [])

    def test_bad_checksum(self):
        c = bytearray(cart())
        c[-2] ^= 1
        self.assertIn("checksum", d.describe(bytes(c))[1][0])

    def test_empty_cart_rejected(self):
        # Declared size matches the (empty) body, but a cart must carry 4096.
        problems = d.describe(dump(d.FMT_32VOICE, []))[1]
        self.assertTrue(any("must carry 4096" in p for p in problems))

    def test_parameter_change_not_checked(self):
        msg = bytes([0xF0, 0x43, 0x10, 0x01, 0x1B, 0x05, 0xF7])
        self.assertEqual(d.describe(msg)[1], [])

    def test_truncated_dump(self):
        self.assertIn("truncated", d.describe(b"\xF0\x43\x00\x09\xF7")[1][0])

    def test_non_yamaha(self):
        self.assertEqual(d.describe(b"\xF0\x41\x10\xF7")[1], [])


class PatchNames(unittest.TestCase):
    def test_names(self):
        names = d.patch_names(cart())
        self.assertEqual(len(names), 32)
        self.assertEqual(names[0], "PATCH 01")

    def test_not_a_cart(self):
        self.assertEqual(d.patch_names(dump(d.FMT_1VOICE, [0] * 155)), [])
        self.assertEqual(d.patch_names(dump(d.FMT_32VOICE, [])), [])


class ResolvePort(unittest.TestCase):
    def test_default_picks_only_output(self):
        self.assertEqual(d.resolve_port(None, ["M4"]), "M4")

    def test_default_refuses_to_guess(self):
        with self.assertRaises(SystemExit):
            d.resolve_port(None, ["A", "B"])
        with self.assertRaises(SystemExit):
            d.resolve_port(None, [])

    def test_substring(self):
        self.assertEqual(d.resolve_port("um", ["UM-ONE", "M4"]), "UM-ONE")
        with self.assertRaises(SystemExit):
            d.resolve_port("m", ["UM-ONE", "M4"])


class Channel(unittest.TestCase):
    def test_range(self):
        self.assertEqual(d.channel("16"), 16)
        for bad in ("0", "17"):
            with self.assertRaises(argparse.ArgumentTypeError):
                d.channel(bad)


class InfoOffline(unittest.TestCase):
    """--info must not touch MIDI, and must fail loudly on a bad file."""

    def run_info(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "bank.syx")
            f.write_bytes(data)
            return d.main(["--info", "--file", str(f)])

    def test_valid(self):
        self.assertIsNone(self.run_info(cart()))

    def test_invalid_exits_nonzero(self):
        c = bytearray(cart())
        c[-2] ^= 1
        self.assertEqual(self.run_info(bytes(c)), 1)
        self.assertEqual(self.run_info(b"junk"), 1)


if __name__ == "__main__":
    unittest.main()
