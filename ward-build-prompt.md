# Ward — build prompt for Claude Code

Copy the **Ground rules** section into `CLAUDE.md` in the repo root.
Paste the **Kickoff prompt** into Claude Code to start.

---

## Ground rules (put this in CLAUDE.md)

### Git and GitHub

- Never add any account, including yourself, as a collaborator on this repository.
  Do not call `gh api` against `/collaborators`, `/invitations`, or any endpoint
  that changes repository access or permissions.
- Never push to `main`. Never force-push to any branch.
- Work on one branch per milestone, named `ward/m0-contracts`, `ward/m1-lab`,
  and so on.
- When a milestone's exit criteria pass, open a pull request and stop.
  Do not merge it. I review and merge.
- Do not create, delete, or rename any remote branch other than your own
  milestone branch.
- Do not modify anything under `.github/` without asking first.
- Stop at the end of every milestone and wait for my review before starting
  the next one.

### Scope discipline

- Do not start a later milestone's work early, even if it seems trivial.
- If a milestone turns out to need a design decision I haven't made, stop and
  ask. Do not pick one and move on.
- Prefer boring, obvious code. This is a diagnostics tool for broken machines;
  clever code that fails in an unclear way is worse than verbose code that
  fails loudly.

### Safety

- Ward's tools must never execute anything on the host machine. Every action
  goes through a `Target`. If a code path could run on the host, that is a bug.
- Never widen the capability tier of an action to make a test pass.

---

## What Ward is

Ward is an AI-driven stand-in for the human who would otherwise sit down in
front of a broken computer.

It gives an agent three things:

- **Eyes** — a picture of what's on the screen
- **Hands** — keyboard and mouse input the machine can't distinguish from real
- **A power button**

Because it works at the peripheral level, it does not need the target machine
to have a working operating system, network, or drivers. It works on BIOS
screens, boot loops, kernel panics, and blue screens.

Phase 1 targets **virtual machines only**. Physical hardware comes later,
behind the same interface, and nothing above the driver layer should have to
change when it arrives.

### Stack

Python 3.11+, `uv` for dependency management, `ruff` for lint, `pytest` for
tests. QEMU/KVM for the virtual targets. No web framework, no database.

---

## Core contract

Everything in Ward talks to a `Target`. One protocol, many drivers.

```python
class Target(Protocol):
    def describe(self) -> TargetDescription: ...
    def capabilities(self) -> Capabilities: ...
    def screenshot(self) -> Image: ...
    def type_text(self, text: str) -> None: ...
    def press(self, combo: list[str]) -> None: ...
    def power(self, action: PowerAction) -> None: ...
    def exec(self, cmd: str) -> ExecResult: ...   # only if capabilities allow
```

`TargetDescription` must carry these fields, because they change what repair
strategy is valid:

- `transport` — how we're connected (`qemu`, `pico-hid`, `ssh`)
- `mode` — `live` (the target OS is running) or `offline` (we're in a rescue
  environment with the target's disk mounted) or `firmware` (BIOS/UEFI screen)
- `os_family` — may be `unknown`
- `bandwidth_class` — `fast` or `slow`

`mode` is the important one. Live repair and offline repair are different
worlds with different commands. An agent that doesn't know which one it's in
will confidently run commands against the wrong system.

---

## Milestones

### M0 — Contracts and skeleton

Define the protocol above, the dataclasses, the capability tiers, and a
`FakeTarget` that returns canned screenshots and records input. No real
drivers.

**Exit:** `pytest` passes against `FakeTarget`. `ward describe` prints the
fake target's description as JSON.

---

### M1 — The broken machine lab

Build reproducible broken VMs. This is the most valuable thing in the whole
project — without it there is nothing to test against and no way to know if
Ward works.

- Build a Debian base image once, keep it read-only.
- A "breaker" library: named faults, each one applied to a fresh copy of the
  base image. Start with five:
  - `fstab-bad-uuid` — boots into emergency shell
  - `grub-missing` — no bootloader
  - `initramfs-corrupt` — kernel panic during boot
  - `rootfs-errors` — needs fsck
  - `network-down` — boots fine, no networking
- Each fault records: how to apply it, how to verify the machine actually
  fails, and what a correct repair looks like.
- Snapshots, so every run starts from an identical state.

**Exit:** `ward lab create fstab-bad-uuid` produces a VM that reproducibly
fails to boot in the same way every time. `ward lab reset` restores it.

---

### M2 — QEMU eyes and hands

`QemuTarget`, implementing `Target` over QMP on a unix socket.

- `screenshot()` via QMP `screendump`, saved as PNG
- `type_text()` and `press()` via `send-key`
- `power()` via `system_reset` and process control
- Hard timeouts on everything. A hung target must raise, never block forever.

**Exit:** a plain Python script can boot a broken VM from M1, screenshot it,
type at the GRUB prompt, and reboot it.

---

### M3 — The surface Claude Code drives

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

Every command supports `--json`. Every command prints what it did to stderr in
plain English.

**Exit:** with `ward` on PATH and a broken VM running, Claude Code can drive
the machine using only its Bash and Read tools. Demonstrate by having it fix
`fstab-bad-uuid` end to end.

---

### M4 — Capability gate and audit log

Three tiers: `observe`, `modify`, `destroy`.

Enforcement lives **inside the CLI**, not in Claude Code's permission prompts.
Claude Code owns its own agent loop and has raw shell access, so any gate it
can route around is not a gate.

- `destroy` actions require a confirmation token that only a human can
  generate.
- Append-only JSONL audit log: timestamp, command, arguments, result,
  screenshot hash.

**Exit:** a `destroy` action without a valid token fails. The audit log
reconstructs a full session.

---

### M5 — Replay and evaluation

- `ward replay <session>` renders a session as an HTML timeline: screenshot,
  action taken, next screenshot.
- `ward eval` runs the agent against every fault in the lab, N times each, and
  reports fixed / not fixed / made worse, with time and action count.

**Exit:** a results table I can quote, and improve against.

---

### M6 — Hardware driver (later, do not start)

`PicoHidTarget`: a Raspberry Pi Pico emulating a USB keyboard, plus a USB HDMI
capture dongle read as a webcam. Same `Target` protocol.

Nothing above the driver layer should need to change. If it does, M0 was wrong.

---

## Kickoff prompt

> We're building Ward. Read `CLAUDE.md` and `SPEC.md` first and follow the
> ground rules in `CLAUDE.md` exactly, especially the git rules.
>
> Start with **M0 only**. Set up the repo skeleton, define the `Target`
> protocol and its supporting types, write `FakeTarget`, and write the tests.
> Do not implement any real driver. Do not touch M1.
>
> When M0's exit criteria pass, open a pull request from `ward/m0-contracts`
> and stop. I'll review before you continue.
>
> Before you write any code, show me the type definitions you're planning and
> wait for me to approve them.
