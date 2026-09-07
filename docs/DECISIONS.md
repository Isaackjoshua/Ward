# Decisions

Small design choices made during a milestone that the spec did not settle.
One entry per decision: what was chosen, and what it costs.

## D1 — The lab base image is a Debian cloud image, not a debootstrap build

*M1.* Building a root filesystem with `debootstrap` needs root on the host.
Ward is a tool for fixing broken machines; it should not need root on the
machine it runs from just to set up its own test fixtures. So the lab
downloads the official Debian `genericcloud` qcow2, checks it against the
published SHA512SUMS, and keeps it read-only. Every fault VM is a qcow2
overlay on that file.

**Cost:** the lab needs network access once, and the base image is whatever
Debian ships rather than something we control byte for byte.

## D2 — Faults are applied by a surgeon VM, not by the host

*M1.* A breaker needs to edit an ext4 filesystem inside a qcow2 image.
Mounting one on the host needs root (`qemu-nbd`, loop devices), and Ward must
not need root. So the lab boots a short-lived *surgeon* VM: the read-only base
image as its own root, the patient's overlay attached as a second disk, and
the breaker script handed to it through cloud-init. The surgeon mounts
`/dev/vdb`, applies the fault with real Debian tools, and powers off.

**Cost:** applying a fault takes a VM boot (tens of seconds) rather than a
file write, and the lab depends on the base image containing the tools the
breakers use.

## D3 — `Image` holds encoded PNG bytes, not a pixel buffer

*M0.* Every consumer of a screenshot either writes it to a file or hashes it.
Neither needs pixels, and carrying a decoded buffer would mean carrying a
decoder. `Image.png` is the encoded file; `width` and `height` come along so
callers can reason about the screen without parsing it.

**Cost:** anything that eventually wants to compare screens pixel by pixel
has to decode first. `ward wait-for-change` therefore compares hashes, which
detects any change but cannot describe it — and cannot ignore one either. On
a text console a blinking cursor is a change, so `wait-for-change` will
usually return within one blink. Treat it as "the machine is still alive",
not as "the machine has finished".

## D4 — Lab patients talk to the serial console, so their screens go quiet

*M1.* Debian's cloud image sets `console=ttyS0`, so once the kernel hands
over to userspace nothing more is drawn on the emulated VGA. A screenshot of
a lab patient shows early boot and then stops changing, even while the
machine is very much still doing things.

This matters, because eyes are the whole point. It was left as it is rather
than half-fixed: putting `tty0` last on the kernel command line moves the
screen output back where an agent can see it, but it also moves systemd's
messages *off* the serial console, and the lab's own `ward lab check` reads
that serial log to decide whether a fault reproduced. Fixing one breaks the
other.

**Cost:** the lab exercises Ward's hands and power button honestly, but only
partly exercises its eyes. A real broken machine, which has no serial console
to prefer, does not have this problem. Getting both — screen output for the
agent and a serial log for the lab — is the first thing to fix before the
lab is used to evaluate an agent's ability to *read* a screen.
