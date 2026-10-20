<div align="center">

# 🎹 dx7cart

**Validate and send DX7 SysEx carts. Know they actually got there.**

Built for the Korg **volca fm2** · works with any DX7-compatible synth · one Python file

</div>

---

## Why

The volca fm2 gives **no sign that it received a cart**, and it keeps the patch you're
on in memory. So a perfect transfer and a total failure look and sound the same
until you change program.

`dx7cart` checks the file before sending and tells you exactly what it sent. If it says
**sent**, the bytes left your interface. Anything still wrong is in the cable, the
jack or the receive channel.

## Install

```sh
git clone https://github.com/gdamdam/dx7-cart-tools.git
cd dx7-cart-tools
pip install .          # installs the `dx7cart` command
```

Or skip installing and run the script directly:
`pip install -r requirements.txt`, then `python3 dx7cart.py ...`.

Wire it up: **interface MIDI OUT → synth MIDI IN**.

## Quick start

Run these in order. Each step proves one link in the chain.

```sh
dx7cart --list                      # 1. find your MIDI port
dx7cart --test-notes                # 2. prove the MIDI path works
dx7cart --info    --file bank.syx   # 3. validate + list patch names
dx7cart --dry-run --file bank.syx   # 4. every check, nothing sent
dx7cart           --file bank.syx   # 5. send (asks first)
```

Then **change program on the synth** and step through 1-32.

## Options

| Flag | What it does |
|---|---|
| `--file PATH` | The `.syx` cart to inspect or send |
| `--port NAME` | MIDI output. A substring is enough (`m4` matches `M4`). If you leave it out and there's only one output, that one is used. Ambiguous matches are an error, never a guess |
| `--channel N` | MIDI channel 1-16 (only used by `--test-notes`) |
| `--list` | List MIDI inputs and outputs |
| `--test-notes` | Play five rising notes to prove the MIDI path |
| `--info` | Validate and print patch names, send nothing |
| `--dry-run` | Run every check, send nothing |
| `--delay SEC` | Pause between messages in multi-message files (default `0.25`) |
| `--yes`, `-y` | Skip the overwrite confirmation |
| `--version` | Print the version |

> **Tip:** edit `DEFAULT_CART`, `DEFAULT_PORT` and `DEFAULT_CHANNEL` at the top of the
> script to run it with fewer arguments.

`--info` works offline: it never touches MIDI, so you can check carts with no synth
connected.

## ⚠️ What gets overwritten

A cart **replaces programs 1-32**, in place, with no undo. A single-voice file only
replaces the current sound (the edit buffer).

On the volca fm2, programs **33-64 are untouched**: a DX7 cart only carries 32 patches
and always lands in the lower bank.

The script asks before sending unless you pass `--yes`.

## What it checks

A bad cart would overwrite 32 presets with garbage, so a failed check **blocks the
send** and exits non-zero.

- ✅ Splits the file into `F0 … F7` messages (some files hold several)
- ✅ Rejects **stray bytes** and messages missing their closing `F7`
- ✅ Identifies each one: 32-voice cart, single voice, or other Yamaha format
- ✅ Verifies the **checksum** of every bulk dump
- ✅ Verifies the **byte count**: declared size matches the payload, and is the right
  size for the format (4096 for a cart, 155 for a single voice)
- ✅ Verifies every payload byte is **7-bit**

A valid 32-voice cart is exactly **4104 bytes** and starts with `F0 43 00 09 20 00`.
Yes, 4 KB is right. It's not a broken download.

## Troubleshooting

<details>
<summary><b>Sent it, but nothing changed</b></summary>

Change program on the synth and step through 1-32. The fm2 keeps the current patch
in memory, so a good load looks like nothing happened. This is almost always the answer.
</details>

<details>
<summary><b>Test notes don't play</b></summary>

Not a cart problem. Check the cable, that you're in the interface's MIDI **OUT** jack,
and that the synth's receive channel matches `--channel`.
</details>

<details>
<summary><b>Test notes play, but the cart doesn't land</b></summary>

Try these in order:

1. Plug the interface **straight into the computer**, not through a USB hub.
2. Try a different MIDI cable.
3. Toggle **LFO Key Sync** on the synth and resend.
4. Try another interface. Cheap USB-MIDI adapters often pass notes fine but mangle
   SysEx, because they only buffer short messages.
</details>

<details>
<summary><b>Using SysEx Librarian instead</b></summary>

Set its **Destination** to your interface. It also shows up as a MIDI *input*, so it's
easy to send the cart into a loopback and see nothing happen. That exact mistake is
why this script exists.
</details>

## Notes

The volca fm2 reads DX7 patch data, but some DX7 banks sound different on it because
its envelopes work differently.

## Development

```sh
python3 -m unittest test_dx7cart
```

## License

[GPL-3.0-or-later](LICENSE)
