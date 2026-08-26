# Ward — project rules

## What Ward is

`ward/` is the Python package behind the project. Ward lets an operator drive a
broken computer through fake peripherals: software reads the machine's screen
as images and sends it keystrokes, so the broken machine — the *patient* —
needs no working OS, no network and no drivers.

Phase one is software only, tested against QEMU virtual machines we
deliberately break. Hardware (a real capture device and a microcontroller
pretending to be a USB keyboard) comes later behind the same interface, so
nothing above the transport layer may know whether it is talking to a VM or
to a physical machine.

## The rule that matters more than anything else in this project

Every operation that touches a target machine must know whether it is in
LIVE mode (the patient OS is running, use its own tools) or OFFLINE mode
(we are in a rescue environment, the patient is a mounted disk, use chroot
and manual edits). These are disjoint worlds with disjoint playbooks. Code
that does not know which world it is in is a bug.

## Git rules, non-negotiable, apply to every session

- Never push. The human pushes after reviewing.
- Never commit to main. Work on branch `m0-skeleton` (and `mN-<name>` for
  later milestones).
- Never add collaborators, never run `gh`, never change remotes or git config.
- Commit locally in small logical commits.

## How work is structured

Milestones run DESIGN → BUILD → REVIEW with no context carried between
phases. See `docs/WORKFLOW.md` for the loop and the prompt templates, and
`docs/ROADMAP.md` for the milestone list.

## Conventions

- Standard library preferred throughout this project.
- `pytest` for tests, `ruff` for linting. Both must be clean before a
  milestone is done.
