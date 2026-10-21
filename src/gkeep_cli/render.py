"""Output rendering: JSON by default, plain text on request."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import cast

from click import echo, style

from gkeep_cli.service import JsonValue


class OutputFormat(StrEnum):
    json = "json"
    text = "text"


def emit(data: object, output_format: OutputFormat, pretty: bool = False) -> None:
    if output_format is OutputFormat.json:
        if pretty:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        return
    use_color = _use_color()
    echo(render(cast(JsonValue, data), color=use_color), color=use_color)


def _use_color(stream: object | None = None) -> bool:
    """Decide whether the destination can render ANSI styling."""
    target = stream if stream is not None else sys.stdout

    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR", "").lower() not in ("", "0", "false"):
        return True
    if os.environ.get("CLICOLOR") == "0":
        return False
    if not getattr(target, "isatty", lambda: False)():
        return False

    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    if os.environ.get("COLORTERM"):
        return True
    if sys.platform == "win32":
        return bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("ANSICON")
            or os.environ.get("ConEmuANSI") == "ON"  # noqa: SIM112
            or os.environ.get("TERM_PROGRAM") == "vscode"
        )
    return _terminfo_colors() > 0


def _terminfo_colors() -> int:
    """Ask terminfo how many colors the terminal claims to support."""
    try:
        import curses
    except ImportError:
        return 0
    try:
        curses.setupterm()
        return curses.tigetnum("colors")
    except (curses.error, OSError):
        return 0


def _style(text: str, *, color: bool, bold: bool = False, fg: str | None = None) -> str:
    if not color:
        return text
    return style(text, bold=bold, fg=fg)


def emit_error(
    message: str,
    output_format: OutputFormat,
    pretty: bool = False,
    hint: str | None = None,
) -> None:
    if output_format is OutputFormat.json:
        payload: dict[str, JsonValue] = {"error": message}
        if hint:
            payload["hint"] = hint
        if pretty:
            text = json.dumps(payload, indent=2, ensure_ascii=False)
        else:
            text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        print(text, file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)
        if hint:
            print(f"hint: {hint}", file=sys.stderr)


def render(data: JsonValue, *, color: bool = False) -> str:
    if _is_note(data):
        return _render_note(cast(dict[str, JsonValue], data), use_color=color)
    if isinstance(data, list) and data and all(_is_note(item) for item in data):
        notes = cast(list[dict[str, JsonValue]], data)
        if not notes:
            return "No notes."
        blocks = [_render_note(note, use_color=color) for note in notes]
        header = _style(f"{len(notes)} note(s)", color=color, bold=True)
        return f"{header}\n\n" + "\n".join(blocks)
    if isinstance(data, list) and data and all(_is_label(item) for item in data):
        labels = cast(list[dict[str, JsonValue]], data)
        return "\n".join(f"{label['id']}  {label['name']}" for label in labels)
    if isinstance(data, list) and data and all(_is_item(item) for item in data):
        items = cast(list[dict[str, JsonValue]], data)
        return "\n".join(_render_item(item, color=color) for item in items)
    if isinstance(data, list) and data and all(isinstance(item, str) for item in data):
        return "\n".join(cast(list[str], data))
    if data == []:
        return "No results."
    return _render_generic(data, indent=0)


def _is_note(value: JsonValue) -> bool:
    return isinstance(value, dict) and "id" in value and "type" in value and "title" in value


def _is_label(value: JsonValue) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"id", "name"}
        and isinstance(value.get("name"), str)
    )


def _is_item(value: JsonValue) -> bool:
    return isinstance(value, dict) and set(value) == {
        "id",
        "text",
        "checked",
        "indented",
    }


def _render_note(note: dict[str, JsonValue], *, use_color: bool = False) -> str:
    title = note.get("title") or "(untitled)"
    flags: list[str] = [str(note.get("type", "text"))]
    color = note.get("color")
    if color and color != "DEFAULT":
        flags.append(str(color))
    for flag in ("pinned", "archived", "trashed"):
        if note.get(flag):
            flags.append(flag)

    lines = [
        _style(f"{title}  [{', '.join(flags)}]", color=use_color, bold=True),
        _style(f"id: {note.get('id')}", color=use_color, bold=True),
    ]

    labels = note.get("labels")
    if isinstance(labels, list) and labels:
        lines.append(
            _style(
                "labels: " + ", ".join(str(label) for label in labels), color=use_color, bold=True
            )
        )

    collaborators = note.get("collaborators")
    if isinstance(collaborators, list) and collaborators:
        lines.append(
            _style(
                "shared with: " + ", ".join(str(c) for c in collaborators),
                color=use_color,
                bold=True,
            )
        )

    timestamps = note.get("timestamps")
    if isinstance(timestamps, dict):
        created = timestamps.get("created")
        updated = timestamps.get("updated")
        if created or updated:
            lines.append(
                _style(f"created: {created}  updated: {updated}", color=use_color, bold=True)
            )

    items = note.get("items")
    if isinstance(items, list):
        if not items:
            lines.append(_style("(no items)", color=use_color, bold=True))
        for raw in items:
            if not isinstance(raw, dict):
                continue
            lines.append(_render_item(raw, color=use_color))
    else:
        text = note.get("text") or ""
        for line in str(text).splitlines() or [""]:
            lines.append(_style(line, color=use_color, fg="green") if line else "")

    return "\n".join(lines)


def _render_item(item: dict[str, JsonValue], *, color: bool = False) -> str:
    mark = "x" if item.get("checked") else " "
    content = _style(str(item.get("text")), color=color, fg="green")
    return f"[{mark}] {content}  ({item.get('id')})"


def _render_generic(data: JsonValue, indent: int) -> str:
    pad = " " * indent
    if isinstance(data, Mapping):
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, Mapping | list) and value:
                lines.append(f"{pad}{key}:")
                lines.append(_render_generic(value, indent + 2))
            else:
                lines.append(f"{pad}{key}: {_scalar(value)}")
        return "\n".join(lines)
    if isinstance(data, Sequence) and not isinstance(data, str):
        if not data:
            return f"{pad}(empty)"
        lines = []
        for entry in data:
            if isinstance(entry, Mapping | list):
                lines.append(f"{pad}-")
                lines.append(_render_generic(entry, indent + 2))
            else:
                lines.append(f"{pad}- {_scalar(entry)}")
        return "\n".join(lines)
    return f"{pad}{_scalar(data)}"


def _scalar(value: JsonValue) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    return str(value)
