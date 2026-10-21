"""Google Keep operations and JSON serialisation.

Wraps `gkeepapi` and returns JSON-ready values. Every mutating call syncs
before writing.
"""

from __future__ import annotations

from typing import Final, TypeAlias, cast

import gkeepapi
import gkeepapi.exception
import gkeepapi.node

from gkeep_cli.errors import (
    AuthError,
    ItemNotFound,
    KeepError,
    LabelNotFound,
    NotAListNote,
    NoteNotFound,
)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

NOTE_COLORS: Final = tuple(color.value for color in gkeepapi.node.ColorValue)
COLOR_BY_NAME: Final = {color.value: color for color in gkeepapi.node.ColorValue}


def serialise_label(label: gkeepapi.node.Label) -> dict[str, JsonValue]:
    return {"id": label.id, "name": label.name}


def serialise_items(note: gkeepapi.node.List) -> list[dict[str, JsonValue]]:
    return [
        {
            "id": item.id,
            "text": item.text,
            "checked": item.checked,
            "indented": item.indented,
        }
        for item in note.items
        if not item.deleted
    ]


def serialise_note(note: gkeepapi.node.TopLevelNode) -> dict[str, JsonValue]:
    created = note.timestamps.created
    updated = note.timestamps.updated
    payload: dict[str, JsonValue] = {
        "id": note.id,
        "title": note.title,
        "type": "list" if isinstance(note, gkeepapi.node.List) else "text",
        "pinned": note.pinned,
        "archived": note.archived,
        "trashed": note.trashed,
        "color": note.color.value if note.color else "DEFAULT",
        "labels": [label.name for label in note.labels.all()],
        "timestamps": {
            "created": created.isoformat() if created else None,
            "updated": updated.isoformat() if updated else None,
        },
        "collaborators": list(note.collaborators.all()),
    }
    if isinstance(note, gkeepapi.node.List):
        payload["items"] = cast(JsonValue, serialise_items(note))
    else:
        payload["text"] = note.text
    return payload


class KeepService:
    """Thin wrapper over `gkeepapi.Keep` with JSON-ready return values."""

    def __init__(self, keep: gkeepapi.Keep) -> None:
        self._keep = keep

    @classmethod
    def connect(cls, email: str, master_token: str) -> KeepService:
        keep = gkeepapi.Keep()
        try:
            keep.authenticate(email, master_token)
        except gkeepapi.exception.LoginException as exc:
            raise AuthError(f"Google Keep login failed: {exc}") from exc
        except gkeepapi.exception.KeepException as exc:
            raise AuthError(f"Google Keep session could not start: {exc}") from exc
        return cls(keep)

    # -- internals ---------------------------------------------------------

    def _sync(self) -> None:
        try:
            self._keep.sync()
        except gkeepapi.exception.KeepException as exc:
            raise KeepError(f"Google Keep sync failed: {exc}") from exc

    def _note(self, note_id: str) -> gkeepapi.node.TopLevelNode:
        self._sync()
        note = self._keep.get(note_id)
        if note is None:
            raise NoteNotFound(f"Note not found: {note_id}")
        return note

    def _list_note(self, note_id: str) -> gkeepapi.node.List:
        note = self._note(note_id)
        if not isinstance(note, gkeepapi.node.List):
            raise NotAListNote(f"Note {note_id} is not a checklist")
        return note

    def _label(self, name: str) -> gkeepapi.node.Label:
        label = self._keep.findLabel(name)
        if label is None:
            raise LabelNotFound(f"Label not found: {name}")
        return label

    @staticmethod
    def _item(note: gkeepapi.node.List, item_id: str) -> gkeepapi.node.ListItem:
        for item in note.items:
            if item.id == item_id and not item.deleted:
                return item
        raise ItemNotFound(f"Item not found in note {note.id}: {item_id}")

    @staticmethod
    def _color(name: str) -> gkeepapi.node.ColorValue:
        key = name.upper()
        color = COLOR_BY_NAME.get(key)
        if color is None:
            raise KeepError(f"Unknown color {name!r}. Valid colors: {', '.join(NOTE_COLORS)}")
        return color

    # -- notes -------------------------------------------------------------

    def list_notes(
        self,
        *,
        query: str | None = None,
        labels: list[str] | None = None,
        colors: list[str] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        trashed: bool = False,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, JsonValue]]:
        if offset < 0:
            raise KeepError(f"offset must be >= 0, got {offset}")
        if limit is not None and limit < 1:
            raise KeepError(f"limit must be >= 1, got {limit}")

        self._sync()
        label_objects = [self._label(name) for name in labels] if labels else None
        color_values = [self._color(name) for name in colors] if colors else None
        notes = list(
            self._keep.find(
                query=query,
                labels=label_objects,
                colors=color_values,
                pinned=pinned,
                archived=archived,
                trashed=trashed,
            )
        )
        notes = notes[offset:]
        if limit is not None:
            notes = notes[:limit]
        return [serialise_note(note) for note in notes]

    def get_note(self, note_id: str) -> dict[str, JsonValue]:
        return serialise_note(self._note(note_id))

    def create_text_note(self, text: str, title: str = "") -> dict[str, JsonValue]:
        note = self._keep.createNote(title, text)
        self._sync()
        return serialise_note(note)

    def create_list_note(
        self, items: list[tuple[str, bool]], title: str = ""
    ) -> dict[str, JsonValue]:
        note = self._keep.createList(title, items)
        self._sync()
        return serialise_note(note)

    def update_note(
        self,
        note_id: str,
        *,
        title: str | None = None,
        text: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        color: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, JsonValue]:
        note = self._note(note_id)

        # Resolve labels before mutating so a typo cannot half-apply the update.
        labels_to_add = [self._label(name) for name in add_labels or []]
        labels_to_remove = [self._label(name) for name in remove_labels or []]

        if title is not None:
            note.title = title
        if text is not None:
            if not isinstance(note, gkeepapi.node.Note):
                raise NotAListNote(f"Note {note_id} is a checklist, so it has no body text")
            note.text = text
        if pinned is not None:
            note.pinned = pinned
        if archived is not None:
            note.archived = archived
        if color is not None:
            note.color = self._color(color)
        for label in labels_to_add:
            note.labels.add(label)
        for label in labels_to_remove:
            note.labels.remove(label)

        self._sync()
        return serialise_note(note)

    def delete_note(self, note_id: str) -> dict[str, JsonValue]:
        """Move a note to the trash. Recoverable with restore_note."""
        note = self._note(note_id)
        note.trash()
        self._sync()
        return {"deleted": note_id, "trashed": True}

    def restore_note(self, note_id: str) -> dict[str, JsonValue]:
        """Take a note back out of the trash."""
        note = self._note(note_id)
        note.untrash()
        self._sync()
        return serialise_note(note)

    def purge_note(self, note_id: str) -> dict[str, JsonValue]:
        """Permanently delete a note. No restore after this."""
        note = self._note(note_id)
        note.delete()
        self._sync()
        return {"purged": note_id}

    # -- list items --------------------------------------------------------

    def list_items(self, note_id: str) -> list[dict[str, JsonValue]]:
        return serialise_items(self._list_note(note_id))

    def add_list_item(self, note_id: str, text: str, checked: bool = False) -> dict[str, JsonValue]:
        note = self._list_note(note_id)
        note.add(text, checked)
        self._sync()
        return serialise_note(note)

    def update_list_item(
        self,
        note_id: str,
        item_id: str,
        *,
        text: str | None = None,
        checked: bool | None = None,
    ) -> dict[str, JsonValue]:
        note = self._list_note(note_id)
        item = self._item(note, item_id)
        if text is not None:
            item.text = text
        if checked is not None:
            item.checked = checked
        self._sync()
        return serialise_note(note)

    def delete_list_item(self, note_id: str, item_id: str) -> dict[str, JsonValue]:
        note = self._list_note(note_id)
        item = self._item(note, item_id)
        item.delete()
        self._sync()
        return serialise_note(note)

    def sort_list_items(self, note_id: str, reverse: bool = False) -> dict[str, JsonValue]:
        note = self._list_note(note_id)
        note.sort_items(reverse=reverse)
        self._sync()
        return serialise_note(note)

    def indent_list_item(
        self, note_id: str, item_id: str, parent_item_id: str
    ) -> dict[str, JsonValue]:
        note = self._list_note(note_id)
        item = self._item(note, item_id)
        parent = self._item(note, parent_item_id)
        parent.indent(item)
        if item.super_list_item_id != parent.id:
            raise KeepError(
                f"Could not indent {item_id} under {parent_item_id}. "
                "gkeepapi refuses to indent a parent item that already has children."
            )
        self._sync()
        return serialise_note(note)

    def dedent_list_item(
        self, note_id: str, item_id: str, parent_item_id: str
    ) -> dict[str, JsonValue]:
        note = self._list_note(note_id)
        item = self._item(note, item_id)
        parent = self._item(note, parent_item_id)
        parent.dedent(item)
        if item.super_list_item_id:
            raise KeepError(f"Item {item_id} is not nested under {parent_item_id}")
        self._sync()
        return serialise_note(note)

    def replace_list_items(
        self, note_id: str, items: list[tuple[str, bool]]
    ) -> dict[str, JsonValue]:
        note = self._list_note(note_id)
        for item in note.items:
            if not item.deleted:
                item.delete()
        for text, checked in items:
            note.add(text, checked)
        self._sync()
        return serialise_note(note)

    # -- labels ------------------------------------------------------------

    def list_labels(self) -> list[dict[str, JsonValue]]:
        self._sync()
        return [serialise_label(label) for label in self._keep.labels()]

    def create_label(self, name: str) -> dict[str, JsonValue]:
        try:
            label = self._keep.createLabel(name)
        except gkeepapi.exception.KeepException as exc:
            raise KeepError(f"Could not create label {name!r}: {exc}") from exc
        self._sync()
        return serialise_label(label)

    def rename_label(self, name: str, new_name: str) -> dict[str, JsonValue]:
        label = self._label(name)
        label.name = new_name
        self._sync()
        return serialise_label(label)

    def delete_label(self, name: str) -> dict[str, JsonValue]:
        label = self._label(name)
        self._keep.deleteLabel(label.id)
        self._sync()
        return {"deleted": name}

    # -- collaborators -----------------------------------------------------

    def list_collaborators(self, note_id: str) -> list[str]:
        return list(self._note(note_id).collaborators.all())

    def add_collaborator(self, note_id: str, email: str) -> dict[str, JsonValue]:
        note = self._note(note_id)
        note.collaborators.add(email)
        self._sync()
        return serialise_note(note)

    def remove_collaborator(self, note_id: str, email: str) -> dict[str, JsonValue]:
        note = self._note(note_id)
        note.collaborators.remove(email)
        self._sync()
        return serialise_note(note)
