"""Rendering an audit log as something a person can read.

A session is a sequence of screen, action, screen. The log has all of it, but
a JSONL file is not how anyone actually reviews a repair. This turns it into
one page: what was on screen, what Ward did next, what happened.

Standard library only — this writes HTML by hand rather than pulling in a
template engine for one page.
"""

from __future__ import annotations

import html
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

_STYLE = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 60rem;
       padding: 2rem 1rem; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
.meta { color: #777; margin-bottom: 2rem; }
.step { border-top: 1px solid #8884; padding: 1rem 0; display: grid;
        grid-template-columns: 14rem 1fr; gap: 1rem; align-items: start; }
.step img { width: 100%; border: 1px solid #8884; background: #000; }
.noshot { color: #888; font-style: italic; }
.cmd { font-family: ui-monospace, monospace; font-weight: 600; }
.tier { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
        padding: 0.1rem 0.4rem; border-radius: 0.3rem; border: 1px solid #8886; }
.tier-destroy { color: #b3261e; border-color: #b3261e; }
.tier-modify { color: #8a6100; border-color: #8a6100; }
.tier-observe { color: #555; }
.failed { color: #b3261e; }
.args { color: #666; font-family: ui-monospace, monospace; font-size: 0.85rem;
        white-space: pre-wrap; word-break: break-word; }
.error { color: #b3261e; font-family: ui-monospace, monospace; }
@media (max-width: 40rem) { .step { grid-template-columns: 1fr; } }
"""


def _relative(path: str, start: Path) -> str:
    """Return a link that works when the HTML is opened from disk."""
    try:
        return os.path.relpath(path, start)
    except ValueError:
        # A different drive on Windows. An absolute path still opens.
        return path


#: Plumbing that is already shown elsewhere on the row, or is never useful.
_BORING_ARGUMENTS = frozenset({"command", "lab_command", "target", "tail"})


def _format_arguments(arguments: dict) -> str:
    interesting = {
        key: value
        for key, value in arguments.items()
        if key not in _BORING_ARGUMENTS and value not in (None, False)
    }
    return ", ".join(f"{key}={value!r}" for key, value in interesting.items())


def _step_html(entry: dict, start: Path) -> str:
    command = html.escape(str(entry.get("command", "?")))
    tier = html.escape(str(entry.get("tier", "observe")))
    timestamp = html.escape(str(entry.get("timestamp", "")))
    target = html.escape(str(entry.get("target", "")))
    mode = html.escape(str(entry.get("mode", "")))
    exit_code = entry.get("exit_code", 0)
    arguments = html.escape(_format_arguments(entry.get("arguments") or {}))

    shot = entry.get("screenshot_path")
    if shot and Path(shot).exists():
        source = html.escape(_relative(shot, start))
        picture = f'<a href="{source}"><img src="{source}" alt="screen"></a>'
    else:
        picture = '<div class="noshot">no screenshot</div>'

    failed = ' class="failed"' if exit_code not in (0, None) else ""
    lines = [
        f'<div class="step"><div>{picture}</div><div>',
        f'<div><span class="cmd"{failed}>{command}</span> '
        f'<span class="tier tier-{tier}">{tier}</span></div>',
        f'<div class="args">{arguments}</div>' if arguments else "",
        f'<div class="meta">{timestamp} · {target} · {mode} mode · '
        f"exit {exit_code}</div>",
    ]
    if entry.get("error"):
        lines.append(f'<div class="error">{html.escape(str(entry["error"]))}</div>')
    lines.append("</div></div>")
    return "\n".join(line for line in lines if line)


def render_timeline(
    entries: Iterable[dict],
    destination: Path,
    *,
    title: str = "Ward session",
) -> Path:
    """Write an HTML timeline of a session and return its path."""
    entries = list(entries)
    destination.parent.mkdir(parents=True, exist_ok=True)
    start = destination.parent

    failures = sum(1 for entry in entries if entry.get("exit_code") not in (0, None))
    destroys = sum(1 for entry in entries if entry.get("tier") == "destroy")
    escaped_title = html.escape(title)

    body = "\n".join(_step_html(entry, start) for entry in entries)
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escaped_title}</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>{escaped_title}</h1>
<p class="meta">{len(entries)} steps · {failures} failed · {destroys} destroy-tier</p>
{body}
</body>
</html>
"""
    destination.write_text(document, encoding="utf-8")
    return destination


def summarise(entries: Sequence[dict]) -> dict[str, object]:
    """Return the numbers worth quoting about a session."""
    return {
        "steps": len(entries),
        "failed": sum(1 for e in entries if e.get("exit_code") not in (0, None)),
        "destroy_actions": sum(1 for e in entries if e.get("tier") == "destroy"),
        "screenshots": sum(1 for e in entries if e.get("screenshot_sha256")),
    }
