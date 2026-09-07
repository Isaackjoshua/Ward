# Roadmap

Milestones run DESIGN → BUILD → REVIEW; see `docs/WORKFLOW.md`. They run in
order, each one landing on `main` once `pytest` and `ruff` are clean.

## M0 — Contracts and skeleton

The `Target` protocol, the dataclasses, the capability tiers, and a
`FakeTarget` that returns canned screenshots and records input. No real
drivers.

**Exit:** `pytest` passes against `FakeTarget`. `ward describe` prints the
fake target's description as JSON.

## M1 — The broken machine lab

Reproducible broken VMs. This is the most valuable thing in the whole
project — without it there is nothing to test against and no way to know if
Ward works.

- Build a Debian base image once, keep it read-only.
- A "breaker" library: named faults, each applied to a fresh copy of the base
  image. Start with five:
  - `fstab-bad-uuid` — boots into emergency shell
  - `grub-missing` — no bootloader
  - `initramfs-corrupt` — kernel panic during boot
  - `rootfs-errors` — needs fsck
  - `network-down` — boots fine, no networking
- Each fault records how to apply it, how to verify the machine actually
  fails, and what a correct repair looks like.
- Snapshots, so every run starts from an identical state.

**Exit:** `ward lab create fstab-bad-uuid` produces a VM that reproducibly
fails to boot the same way every time. `ward lab reset` restores it.

## M2 — QEMU eyes and hands

`QemuTarget`, implementing `Target` over QMP on a unix socket.

- `screenshot()` via QMP `screendump`, saved as PNG
- `type_text()` and `press()` via `send-key`
- `power()` via `system_reset` and process control
- Hard timeouts on everything. A hung target must raise, never block forever.

**Exit:** a plain Python script can boot a broken VM from M1, screenshot it,
type at the GRUB prompt, and reboot it.

## M3 — The surface Claude Code drives

A CLI, because Claude Code can already run shell commands and read PNG files.
No MCP server yet.

```
ward screenshot [--out PATH]        writes a PNG, prints the path
ward type "text"
ward key ctrl-alt-f2
ward wait-for-change [--timeout 30] blocks until the screen changes
ward power reset|on|off
ward describe
```

Every command supports `--json`. Every command prints what it did to stderr
in plain English.

**Exit:** with `ward` on PATH and a broken VM running, Claude Code can drive
the machine using only its Bash and Read tools. Demonstrated by fixing
`fstab-bad-uuid` end to end.

## M4 — Capability gate and audit log

Three tiers: `observe`, `modify`, `destroy`. Enforcement lives inside the
CLI, not in Claude Code's permission prompts.

- `destroy` actions require a confirmation token only a human can generate.
- Append-only JSONL audit log: timestamp, command, arguments, result,
  screenshot hash.

**Exit:** a `destroy` action without a valid token fails. The audit log
reconstructs a full session.

## M5 — Replay and evaluation

- `ward replay <session>` renders a session as an HTML timeline: screenshot,
  action taken, next screenshot.
- `ward eval` runs the agent against every fault in the lab, N times each,
  and reports fixed / not fixed / made worse, with time and action count.

**Exit:** a results table worth quoting and improving against.

## M6 — Hardware driver (later, do not start)

`PicoHidTarget`: a Raspberry Pi Pico emulating a USB keyboard, plus a USB
HDMI capture dongle read as a webcam. Same `Target` protocol.

Nothing above the driver layer should need to change. If it does, M0 was
wrong.
