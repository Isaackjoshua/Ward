"""Making a lab patient's screen show what a person would actually see.

Debian's cloud image puts ``console=ttyS0`` last on the kernel command line.
Everything the kernel prints still reaches every console, but ``/dev/console``
— where systemd, the initramfs and any emergency shell write — becomes the
serial port. The emulated display therefore freezes a second or two into
boot and shows nothing afterwards.

That is fatal for Ward, whose entire premise is looking at the screen. So
every patient is prepared before it is broken: ``tty0`` goes last, and the
screen becomes ``/dev/console``.

The lab then needs its own way to read what happened, because it used to read
exactly those systemd messages off the serial port. It gets a second serial
port and a small service that copies the journal to it. The operator gets the
screen; the lab gets the journal; neither is taking the other's channel.
"""

from __future__ import annotations

from ward.lab.faults import PATIENT_MOUNT

#: The kernel command line every patient is given. ``tty0`` is last, and that
#: is the whole point: the last console listed is the one userspace writes to.
CONSOLE_ARGUMENTS = "console=ttyS0,115200n8 console=tty0"

#: The unit that copies the journal to the second serial port.
JOURNAL_UNIT = "ward-serial-journal.service"

#: Lab patients get a root password, because without one the emergency shell
#: says "Cannot open access to console, the root account is locked" and there
#: is nothing an operator — or an agent — can do but watch it reboot. A real
#: broken machine has a root password or rescue media; a locked one is not a
#: harder test, it is an impossible one.
#:
#: This is safe only because lab patients have no network and exist to be
#: broken. Nothing outside the lab may ever do this.
ROOT_PASSWORD = "ward"
_ROOT_PASSWORD_HASH = (
    "$6$wardlabsalt$QoZMJ/ucm9bcGBs1Q9gQzxq4IgVBOOzd."
    "EsggYJnO51tXRGmXGzBurYFRNM8wwKi/XnOZ2YfY7u5YNGf/Mx6U/"
)

PREPARE_SCRIPT = rf"""
set -eu
root={PATIENT_MOUNT}

# Every boot entry gets the same consoles, in the same order, with whatever
# the image shipped stripped out first so this cannot accumulate.
grub_cfg="$root/boot/grub/grub.cfg"
[ -f "$grub_cfg" ]
sed -i -E '/^[[:space:]]*linux/ {{
    s/[[:space:]]+console=[^[:space:]]+//g
    s/$/ {CONSOLE_ARGUMENTS}/
}}' "$grub_cfg"
grep -q 'console=tty0$' "$grub_cfg"

# And in the source of truth too, so a repair that runs update-grub does not
# quietly undo it.
default="$root/etc/default/grub"
if [ -f "$default" ]; then
  line='GRUB_CMDLINE_LINUX_DEFAULT="{CONSOLE_ARGUMENTS}"'
  sed -i -E "s|^GRUB_CMDLINE_LINUX_DEFAULT=.*|$line|" "$default"
fi

# The screen has taken systemd's output, so give the lab a channel of its own.
# IgnoreOnIsolate matters: dropping to emergency mode isolates that target and
# would otherwise stop this service right as it becomes interesting.
unit="$root/etc/systemd/system/{JOURNAL_UNIT}"
cat > "$unit" <<'UNIT'
[Unit]
Description=Ward: copy the journal to the second serial port
DefaultDependencies=no
After=systemd-journald.service
IgnoreOnIsolate=yes

[Service]
Type=simple
ExecStart=/bin/sh -c 'journalctl --boot --follow --no-pager > /dev/ttyS1'
Restart=always
RestartSec=1

[Install]
WantedBy=sysinit.target
UNIT

# systemctl enable needs a running system, so link it by hand.
mkdir -p "$root/etc/systemd/system/sysinit.target.wants"
ln -sf "../{JOURNAL_UNIT}" \
  "$root/etc/systemd/system/sysinit.target.wants/{JOURNAL_UNIT}"
[ -L "$root/etc/systemd/system/sysinit.target.wants/{JOURNAL_UNIT}" ]

# Give root a password, or the emergency shell refuses to open and there is
# nothing to repair the machine with. Edited straight into /etc/shadow: there
# is no running system here to run passwd against.
shadow="$root/etc/shadow"
[ -f "$shadow" ]
sed -i 's|^root:[^:]*:|root:{_ROOT_PASSWORD_HASH}:|' "$shadow"
grep -q '^root:\$6\$wardlabsalt' "$shadow"
"""
