from __future__ import annotations

import pytest

from gkeep_cli.render import _use_color, render
from gkeep_cli.service import JsonValue


def note_payload(**overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": "note-1",
        "title": "Shopping",
        "type": "text",
        "pinned": False,
        "archived": False,
        "trashed": False,
        "color": "DEFAULT",
        "labels": ["travel"],
        "timestamps": {"created": "2026-01-01T00:00:00+00:00", "updated": None},
        "collaborators": ["friend@example.com"],
        "text": "Milk\nEggs",
    }
    payload.update(overrides)
    return payload


def test_text_note_has_no_indentation() -> None:
    output = render(note_payload())

    for line in output.splitlines():
        assert not line.startswith(" ")


def test_list_items_have_no_indentation() -> None:
    output = render(
        note_payload(
            type="list",
            text=None,
            items=[
                {"id": "item-1", "text": "Fruit", "checked": False, "indented": False},
                {"id": "item-2", "text": "Apple", "checked": True, "indented": True},
            ],
        )
    )

    assert "[ ] Fruit  (item-1)" in output
    assert "[x] Apple  (item-2)" in output
    for line in output.splitlines():
        assert not line.startswith(" ")


def test_multiline_body_keeps_its_own_lines() -> None:
    output = render(note_payload(text="first\nsecond"))

    assert "first" in output
    assert "second" in output


def test_color_output_uses_bold_and_green() -> None:
    output = render(note_payload(), color=True)

    assert "\x1b[1m" in output
    assert "\x1b[32m" in output
    assert "\x1b[0m" in output


def test_plain_output_has_no_ansi_codes() -> None:
    assert "\x1b[" not in render(note_payload())


class FakeStream:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def test_color_disabled_for_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)

    assert _use_color(FakeStream(tty=False)) is False


def test_no_color_overrides_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert _use_color(FakeStream(tty=True)) is False


def test_force_color_overrides_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert _use_color(FakeStream(tty=False)) is True


def test_item_list_has_no_indentation() -> None:
    output = render([{"id": "item-1", "text": "Fruit", "checked": False, "indented": True}])

    assert output == "[ ] Fruit  (item-1)"


def test_collaborator_list_prints_one_email_per_line() -> None:
    output = render(["first@example.com", "second@example.com"])

    assert output == "first@example.com\nsecond@example.com"
