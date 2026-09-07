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

Every operation that touches a target machine must know which mode it is in:

- **LIVE** — the patient OS is running; use its own tools.
- **OFFLINE** — we are in a rescue environment, the patient is a mounted
  disk; use chroot and manual edits.
- **FIRMWARE** — we are at a BIOS/UEFI screen; there is no OS to talk to.

These are disjoint worlds with disjoint playbooks. Code that does not know
which world it is in is a bug. The mode travels on `TargetDescription` so no
caller has to infer it.

## Git rules, non-negotiable, apply to every session

- Work on `main`. Commit in small logical commits and push each milestone's
  work to `origin main` as it lands.
- Never force-push. Never rewrite published history.
- Do not add anyone as an author or co-author on a commit. Commits are
  authored by the repository owner alone — no `Co-Authored-By` trailers and no
  agent attribution in commit messages or pull request bodies.
- Never add collaborators, never touch `/collaborators` or `/invitations` or
  any endpoint that changes repository access, never change remotes or git
  config.
- Do not modify anything under `.github/` without asking first.

## Scope discipline

- Milestones run in order. Do not skip one, and do not leave a milestone's
  exit criteria unmet before moving on to the next.
- `pytest` and `ruff` must be clean before a milestone is called done.
- If a milestone needs a design decision the human has not made, pick the
  smallest reversible option and record the choice in `docs/DECISIONS.md`.
- Prefer boring, obvious code. This is a diagnostics tool for broken machines;
  clever code that fails unclearly is worse than verbose code that fails
  loudly.

## Safety

- Ward's tools must never execute anything on the host machine. Every action
  goes through a `Target`. If a code path could run on the host, that is a bug.
- Never widen the capability tier of an action to make a test pass.

## How work is structured

Milestones run DESIGN → BUILD → REVIEW with no context carried between
phases. See `docs/WORKFLOW.md` for the loop and the prompt templates, and
`docs/ROADMAP.md` for the milestone list. `SPEC.md` is the contract these
milestones build toward.

## Conventions

- Standard library preferred throughout this project.
- `pytest` for tests, `ruff` for linting. Both must be clean before a
  milestone is done.
