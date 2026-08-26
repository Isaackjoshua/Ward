# Roadmap

- **M0 skeleton** (this session) — repository, tooling, agents, docs. No features.
- **M1 patient lab** — scripts that build a QEMU VM and break it in reproducible
  named ways (wipe bootloader, corrupt fstab, break initramfs, remove a
  driver), each resettable by name.
- **M2 Channel interface** — abstract `Channel` with `screenshot()`, `send_keys()`,
  `send_text()`, `power_cycle()`; `QemuChannel` implements it; nothing above
  this layer knows it is talking to a VM.
- **M3 screen reading** — screenshot to structured state (BIOS, GRUB, kernel
  panic, login prompt, shell, Windows recovery, blank), idle or changing,
  text on screen, confidence score, "unknown" as a first-class answer.
- **M4 MCP server** — exposing the Channel as tools to Claude Code.
- **M5 operator rules** — LIVE and OFFLINE playbooks as prose, plus a session log
  of every keystroke sent and every screen seen.
- **M6 HidChannel** — same interface, real capture device and microcontroller.
