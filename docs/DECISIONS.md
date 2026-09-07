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

## D4 — The screen belongs to the operator; the lab reads a second serial port

*M1.* Debian's cloud image puts `console=ttyS0` last on the kernel command
line. Every console still receives what the kernel prints, but `/dev/console`
— where systemd, the initramfs and any emergency shell write — becomes the
serial port. The emulated display froze a second or two into boot and showed
nothing afterwards, which is fatal for a tool whose whole premise is looking
at the screen.

Every patient is now *prepared* before it is broken: `tty0` goes last, so the
display is `/dev/console` and shows what a person standing at the machine
would see.

That alone would have cost the lab its failure signatures, because
`ward lab check` read exactly those systemd messages off the serial port. So
a prepared patient also gets a second serial port and a small service that
copies its journal there. The operator gets the screen, the lab gets the
journal, and neither is taking the other's channel. The service is marked
`IgnoreOnIsolate=yes`, because dropping to emergency mode isolates that
target and would otherwise kill the tap exactly when it becomes interesting.

**Cost:** patients are no longer a stock Debian image — Ward edits
`grub.cfg` and installs one unit before breaking anything. A repair that
regenerates the boot configuration keeps the console order, because
`/etc/default/grub` is edited too.

## D5 — Lab patients have a root password

*M1.* Debian's cloud image ships with root locked, so `sulogin` refuses to
start and an emergency shell says "Cannot open access to console, the root
account is locked" and reboots. An agent could see the failure and do nothing
about it.

A machine nobody can log into is not a harder test than a real broken
machine, it is an impossible one. Prepared patients get the root password
`ward`, written straight into `/etc/shadow`.

**Cost:** a known password in the repository. It is safe only because lab
patients have no network and exist to be destroyed. Nothing outside the lab
may ever do this, and no real target should be assumed to be this
co-operative.
