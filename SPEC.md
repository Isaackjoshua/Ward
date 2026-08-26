# Ward — specification

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

## Stack

Python 3.11+, `uv` for dependency management, `ruff` for lint, `pytest` for
tests. QEMU/KVM for the virtual targets. No web framework, no database.

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
    def exec(self, cmd: str) -> ExecResult:  # only if capabilities allow
        ...
```

`TargetDescription` carries these fields, because they change what repair
strategy is valid:

- `transport` — how we're connected (`qemu`, `pico-hid`, `ssh`, `fake`)
- `mode` — `live` (the target OS is running), `offline` (we're in a rescue
  environment with the target's disk mounted), or `firmware` (BIOS/UEFI
  screen)
- `os_family` — may be `unknown`
- `bandwidth_class` — `fast` or `slow`

`mode` is the important one. Live repair and offline repair are different
worlds with different commands. An agent that doesn't know which one it's in
will confidently run commands against the wrong system.

## Capability tiers

Three tiers: `observe`, `modify`, `destroy`. A target declares the most
dangerous tier it will accept via `Capabilities.max_tier`. Per-command
enforcement, the confirmation token for `destroy`, and the audit log all
arrive in M4 and live **inside the CLI** — Claude Code owns its own agent
loop and has raw shell access, so any gate it can route around is not a gate.

## Safety

Ward's tools must never execute anything on the host machine. Every action
goes through a `Target`. If a code path could run on the host, that is a bug.
Never widen the capability tier of an action to make a test pass.

## Milestones

See `docs/ROADMAP.md`.
