from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import gkeepapi
import gkeepapi.node
import pytest

from gkeep_cli.errors import (
    ItemNotFound,
    KeepError,
    LabelNotFound,
    NotAListNote,
    NoteNotFound,
)
from gkeep_cli.service import JsonValue, KeepService, serialise_note


def items_of(payload: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    value = payload["items"]
    assert isinstance(value, list)
    return [cast(dict[str, JsonValue], entry) for entry in value]


class FakeKeep:
    """In-memory stand-in for gkeepapi.Keep, covering the calls KeepService makes."""

    def __init__(self) -> None:
        self.notes: dict[str, gkeepapi.node.TopLevelNode] = {}
        self._labels: dict[str, gkeepapi.node.Label] = {}
        self.deleted_labels: list[str] = []

    def sync(self) -> None:
        pass

    def get(self, note_id: str) -> gkeepapi.node.TopLevelNode | None:
        return self.notes.get(note_id)

    def find(
        self,
        query: str | None = None,
        labels: list[Any] | None = None,
        colors: list[Any] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        trashed: bool = False,
    ) -> Iterator[gkeepapi.node.TopLevelNode]:
        for note in self.notes.values():
            if query and query not in note.title and query not in getattr(note, "text", ""):
                continue
            if pinned is not None and note.pinned != pinned:
                continue
            if archived is not None and note.archived != archived:
                continue
            if note.trashed != trashed:
                continue
            if labels and not any(note.labels.get(label.id) for label in labels):
                continue
            if colors and note.color not in colors:
                continue
            yield note

    def labels(self) -> list[gkeepapi.node.Label]:
        return list(self._labels.values())

    def findLabel(self, name: str) -> gkeepapi.node.Label | None:
        for label in self._labels.values():
            if label.name == name:
                return label
        return None

    def createLabel(self, name: str) -> gkeepapi.node.Label:
        label = gkeepapi.node.Label()
        label.name = name
        self._labels[name] = label
        return label

    def deleteLabel(self, label_id: str) -> None:
        for name, label in list(self._labels.items()):
            if label.id == label_id:
                del self._labels[name]
                self.deleted_labels.append(label_id)

    def createNote(self, title: str, text: str) -> gkeepapi.node.Note:
        note = gkeepapi.node.Note()
        note.title = title
        note.text = text
        self.notes[note.id] = note
        return note

    def createList(self, title: str, items: list[tuple[str, bool]]) -> gkeepapi.node.List:
        note = gkeepapi.node.List()
        note.title = title
        for text, checked in items:
            note.add(text, checked)
        self.notes[note.id] = note
        return note


@pytest.fixture
def keep() -> FakeKeep:
    return FakeKeep()


@pytest.fixture
def service(keep: FakeKeep) -> KeepService:
    return KeepService(keep)


def test_serialise_text_note() -> None:
    note = gkeepapi.node.Note()
    note.title = "Shopping"
    note.text = "Milk"

    payload = serialise_note(note)

    assert payload["type"] == "text"
    assert payload["text"] == "Milk"
    assert payload["color"] == "DEFAULT"
    assert "items" not in payload


def test_serialise_list_note_includes_items() -> None:
    note = gkeepapi.node.List()
    note.title = "Packing"
    note.add("Passport", True)

    payload = serialise_note(note)

    assert payload["type"] == "list"
    items = items_of(payload)
    assert items[0]["text"] == "Passport"
    assert items[0]["checked"] is True


def test_connect_uses_authenticate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeKeepClient:
        def authenticate(self, email: str, master_token: str) -> None:
            calls.append((email, master_token))

        def resume(self, email: str, master_token: str) -> None:
            pytest.fail("resume is the old name")

    monkeypatch.setattr(gkeepapi, "Keep", FakeKeepClient)

    service = KeepService.connect("me@example.com", "aas_et/token")

    assert isinstance(service, KeepService)
    assert calls == [("me@example.com", "aas_et/token")]


def test_create_text_note(service: KeepService) -> None:
    payload = service.create_text_note("Milk", "Shopping")

    assert payload["title"] == "Shopping"
    assert payload["text"] == "Milk"


def test_create_list_note(service: KeepService) -> None:
    payload = service.create_list_note([("Milk", False), ("Eggs", True)], "Shopping")

    items = items_of(payload)
    checked = {item["text"]: item["checked"] for item in items}
    assert checked == {"Milk": False, "Eggs": True}


def test_list_notes_limit(service: KeepService) -> None:
    for index in range(3):
        service.create_text_note(f"note {index}", f"title {index}")

    assert len(service.list_notes()) == 3
    assert len(service.list_notes(limit=2)) == 2


def test_list_notes_offset_and_limit(service: KeepService) -> None:
    for index in range(5):
        service.create_text_note(f"body {index}", f"title {index}")

    page = service.list_notes(offset=1, limit=2)

    assert [note["title"] for note in page] == ["title 1", "title 2"]
    assert len(service.list_notes(offset=4)) == 1
    assert service.list_notes(offset=10) == []


def test_list_notes_rejects_negative_offset(service: KeepService) -> None:
    with pytest.raises(KeepError, match="offset must be"):
        service.list_notes(offset=-1)


def test_list_notes_rejects_zero_limit(service: KeepService) -> None:
    with pytest.raises(KeepError, match="limit must be"):
        service.list_notes(limit=0)


def test_get_missing_note(service: KeepService) -> None:
    with pytest.raises(NoteNotFound):
        service.get_note("nope")


def test_update_note_color(service: KeepService) -> None:
    note = service.create_text_note("text", "title")

    updated = service.update_note(str(note["id"]), color="red")

    assert updated["color"] == "RED"


def test_update_note_rejects_unknown_color(service: KeepService) -> None:
    note = service.create_text_note("text", "title")

    with pytest.raises(KeepError, match="Unknown color"):
        service.update_note(str(note["id"]), color="chartreuse")


def test_update_note_checks_labels_before_mutating(service: KeepService, keep: FakeKeep) -> None:
    note = service.create_text_note("text", "title")

    with pytest.raises(LabelNotFound):
        service.update_note(str(note["id"]), title="new title", add_labels=["missing"])

    stored = keep.get(str(note["id"]))
    assert stored is not None
    assert stored.title == "title"


def test_update_note_adds_and_removes_labels(service: KeepService) -> None:
    note = service.create_text_note("text", "title")
    service.create_label("travel")

    updated = service.update_note(str(note["id"]), add_labels=["travel"])
    assert updated["labels"] == ["travel"]

    updated = service.update_note(str(note["id"]), remove_labels=["travel"])
    assert updated["labels"] == []


def test_update_note_rejects_text_on_list(service: KeepService) -> None:
    note = service.create_list_note([("Milk", False)], "Shopping")

    with pytest.raises(NotAListNote):
        service.update_note(str(note["id"]), text="nope")


def test_delete_and_restore_note(service: KeepService) -> None:
    note = service.create_text_note("text", "title")
    note_id = str(note["id"])

    trashed = service.delete_note(note_id)
    assert trashed == {"deleted": note_id, "trashed": True}
    assert service.get_note(note_id)["trashed"] is True

    restored = service.restore_note(note_id)
    assert restored["trashed"] is False

    assert service.purge_note(note_id) == {"purged": note_id}


def test_add_update_delete_item(service: KeepService) -> None:
    note = service.create_list_note([("Milk", False)], "Shopping")
    note_id = str(note["id"])

    added = service.add_list_item(note_id, "Eggs")
    items = items_of(added)
    item_id = str(items[-1]["id"])

    updated = service.update_list_item(note_id, item_id, text="Eggs", checked=True)
    items = items_of(updated)
    assert items[-1]["checked"] is True

    removed = service.delete_list_item(note_id, item_id)
    items = items_of(removed)
    assert [item["id"] for item in items] == [item["id"] for item in items if item["id"] != item_id]
    assert len(items) == 1


def test_item_missing(service: KeepService) -> None:
    note = service.create_list_note([("Milk", False)], "Shopping")

    with pytest.raises(ItemNotFound):
        service.update_list_item(str(note["id"]), "missing", checked=True)


def test_replace_items(service: KeepService) -> None:
    note = service.create_list_note([("Milk", False), ("Eggs", False)], "Shopping")

    replaced = service.replace_list_items(str(note["id"]), [("Bread", True)])

    items = items_of(replaced)
    assert [(item["text"], item["checked"]) for item in items] == [("Bread", True)]


def test_sort_items(service: KeepService) -> None:
    note = service.create_list_note([("Zucchini", False), ("Apple", False)], "Shopping")

    sorted_note = service.sort_list_items(str(note["id"]))

    items = items_of(sorted_note)
    assert [item["text"] for item in items] == ["Apple", "Zucchini"]


def test_indent_and_dedent(service: KeepService) -> None:
    note = service.create_list_note([("Fruit", False), ("Apple", False)], "Shopping")
    note_id = str(note["id"])
    items = items_of(note)
    parent_id = str(items[0]["id"])
    child_id = str(items[1]["id"])

    indented = service.indent_list_item(note_id, child_id, parent_id)
    items = items_of(indented)
    child = next(item for item in items if item["id"] == child_id)
    assert child["indented"] is True

    dedented = service.dedent_list_item(note_id, child_id, parent_id)
    items = items_of(dedented)
    child = next(item for item in items if item["id"] == child_id)
    assert child["indented"] is False


def test_list_items(service: KeepService) -> None:
    note = service.create_list_note([("Milk", False), ("Eggs", True)], "Shopping")

    items = service.list_items(str(note["id"]))

    assert {item["text"]: item["checked"] for item in items} == {
        "Milk": False,
        "Eggs": True,
    }


def test_list_items_rejects_text_note(service: KeepService) -> None:
    note = service.create_text_note("body", "title")

    with pytest.raises(NotAListNote):
        service.list_items(str(note["id"]))


def test_list_collaborators(service: KeepService) -> None:
    note = service.create_text_note("body", "title")
    note_id = str(note["id"])

    service.add_collaborator(note_id, "friend@example.com")

    assert service.list_collaborators(note_id) == ["friend@example.com"]


def test_labels_lifecycle(service: KeepService) -> None:
    created = service.create_label("travel")
    assert created["name"] == "travel"
    assert service.list_labels() == [created]

    renamed = service.rename_label("travel", "trips")
    assert renamed["name"] == "trips"

    assert service.delete_label("trips") == {"deleted": "trips"}
    assert service.list_labels() == []


def test_missing_label(service: KeepService) -> None:
    with pytest.raises(LabelNotFound):
        service.rename_label("nope", "still-nope")


def test_collaborators(service: KeepService) -> None:
    note = service.create_text_note("text", "shared")

    added = service.add_collaborator(str(note["id"]), "friend@example.com")
    assert added["collaborators"] == ["friend@example.com"]

    removed = service.remove_collaborator(str(note["id"]), "friend@example.com")
    assert removed["collaborators"] == []
