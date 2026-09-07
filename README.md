# Ward

Ward is an AI-driven stand-in for the person who would otherwise sit down in
front of a broken computer.

It gives an agent three things:

- **Eyes** — a picture of what is on the screen
- **Hands** — keyboard input the machine cannot distinguish from real
- **A power button**

Because it works at the peripheral level, the target needs no working
operating system, no network and no drivers. It works on BIOS screens, boot
loops, kernel panics and blue screens.

Phase one targets virtual machines. Hardware comes later behind the same
interface — see `SPEC.md` for the contract and `docs/ROADMAP.md` for where
that sits.

## The one rule

Every operation knows which world it is in:

| Mode | What it means | How you fix things |
| --- | --- | --- |
| `live` | the patient OS is running | use its own tools |
| `offline` | you are in a rescue environment with the patient's disk mounted | chroot and edit by hand |
| `firmware` | you are at a BIOS/UEFI screen | there is no OS to talk to |

Live repair and offline repair are different worlds with different commands.
An agent that does not know which one it is in will confidently run commands
against the wrong system. The mode travels on `TargetDescription`, and Ward
will not guess it for you.

## Getting started

Needs Python 3.11+, `qemu-system-x86_64`, `qemu-img` and `xorriso`. On Debian
or Ubuntu:

```
sudo apt install qemu-system-x86 qemu-utils xorriso
```

Then:

```
uv pip install -e .            # or: pip install -e .
ward lab fetch-base            # ~340 MB, once, checksum-verified
ward lab create fstab-bad-uuid # boots a VM to break one; takes a few minutes
ward lab boot fstab-bad-uuid   # start it and attach
ward screenshot                # writes a PNG, prints the path
```

From there you are driving the machine:

```
ward type "e2fsck -y /dev/vda1" --enter
ward key ctrl-alt-f2
ward wait-for-change --timeout 60
ward confirm "power reset"     # a human mints the token
ward power reset --confirm <token>
```

Every command prints JSON on stdout and plain English on stderr, so an agent
reads one and a person reads the other.

## The lab

`ward lab` builds machines that are broken in known, repeatable ways. Without
it there is nothing to test Ward against and no way to know whether it works.

| Fault | What it does | Repair happens in |
| --- | --- | --- |
| `fstab-bad-uuid` | `/etc/fstab` names a root that does not exist | `live` |
| `grub-missing` | boot sector wiped, `/boot/grub` gone | `offline` |
| `initramfs-corrupt` | initrd images zeroed; the kernel panics | `offline` |
| `rootfs-errors` | primary superblock zeroed; needs `fsck` | `offline` |
| `network-down` | boots fine, every networking unit masked | `live` |

```
ward lab faults                 # the library, with repair notes
ward lab check fstab-bad-uuid   # boot it and confirm it fails as described
ward lab reset                  # every machine back to freshly broken
```

Each machine keeps a `pristine.qcow2` taken the moment it was broken, so
`reset` is a file copy and every run starts from a byte-identical machine.

One honest caveat: Debian's cloud image sends its console to the serial port,
so a lab patient's *screen* stops changing once early boot is over. The lab
exercises Ward's hands and power button faithfully, and its eyes only partly.
See D4 in `docs/DECISIONS.md` for why that was not papered over.

## Reviewing a run

```
ward audit --tail 20   # the append-only log
ward replay            # renders it as an HTML timeline
```

And to grade an agent across the whole lab:

```
ward confirm eval
ward eval "my-agent-command" --runs 3 --confirm <token>
```

Three outcomes, and the third is the one that matters: **fixed**, **not
fixed**, and **made worse**. An agent that fixes four machines and destroys
the fifth is not an 80% agent.

## Safety

Ward's tools never execute anything on the host. Every action goes through a
`Target`; if a code path could run on the host, that is a bug.

Destructive commands need a single-use, scoped, expiring token from `ward
confirm`. That gate lives inside the CLI rather than in an agent's permission
prompts, because an agent with shell access can route around anything it owns.
It is a speed bump with a paper trail, and it is worth being honest that this
is all it is.
