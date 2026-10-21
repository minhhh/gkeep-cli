from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from gkeep_cli import cli
from gkeep_cli.auth import Credentials
from gkeep_cli.errors import AuthError
from gkeep_cli.service import JsonValue

runner = CliRunner()


def note_payload(**overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": "note-1",
        "title": "Shopping",
        "type": "text",
        "pinned": False,
        "archived": False,
        "trashed": False,
        "color": "DEFAULT",
        "labels": [],
        "timestamps": {"created": "2026-01-01T00:00:00+00:00", "updated": None},
        "collaborators": [],
        "text": "Milk",
    }
    payload.update(overrides)
    return payload


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.notes: list[dict[str, JsonValue]] = [note_payload()]

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    def list_notes(self, **kwargs: object) -> list[dict[str, JsonValue]]:
        self._record("list_notes", **kwargs)
        return self.notes

    def get_note(self, note_id: str) -> dict[str, JsonValue]:
        self._record("get_note", note_id)
        return self.notes[0]

    def create_text_note(self, text: str, title: str) -> dict[str, JsonValue]:
        self._record("create_text_note", text, title)
        return note_payload(title=title, text=text)

    def create_list_note(self, items: list[tuple[str, bool]], title: str) -> dict[str, JsonValue]:
        self._record("create_list_note", items, title)
        return note_payload(type="list", title=title)

    def delete_note(self, note_id: str) -> dict[str, JsonValue]:
        self._record("delete_note", note_id)
        return {"deleted": note_id, "trashed": True}

    def list_items(self, note_id: str) -> list[dict[str, JsonValue]]:
        self._record("list_items", note_id)
        return [{"id": "item-1", "text": "Milk", "checked": False, "indented": False}]

    def list_collaborators(self, note_id: str) -> list[str]:
        self._record("list_collaborators", note_id)
        return ["friend@example.com"]

    def list_labels(self) -> list[dict[str, JsonValue]]:
        self._record("list_labels")
        return [{"id": "label-1", "name": "travel"}]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(cli, "_connect", lambda: fake)
    return fake


def test_notes_list_defaults_to_json(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "list", "--query", "milk"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["title"] == "Shopping"
    assert service.calls == [
        (
            "list_notes",
            (),
            {
                "query": "milk",
                "labels": None,
                "colors": None,
                "pinned": None,
                "archived": None,
                "trashed": False,
                "offset": 0,
                "limit": None,
            },
        )
    ]


def test_json_is_compact_by_default(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "list"])

    assert result.exit_code == 0
    assert "\n" not in result.stdout.strip()
    assert '", "' not in result.stdout


def test_pretty_flag_indents_json(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "list", "--pretty"])

    assert result.exit_code == 0
    assert result.stdout.startswith("[\n  {")
    assert '\n    "id": "note-1"' in result.stdout


def test_notes_list_passes_offset_and_limit(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "list", "--offset", "10", "--limit", "5"])

    assert result.exit_code == 0
    assert service.calls[0][2]["offset"] == 10
    assert service.calls[0][2]["limit"] == 5


def test_notes_list_rejects_negative_offset(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "list", "--offset", "-1"])

    assert result.exit_code != 0
    assert service.calls == []


def test_notes_list_text_format(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "list", "--format", "text"])

    assert result.exit_code == 0
    assert "Shopping" in result.stdout
    assert "[x]" not in result.stdout


def test_format_from_environment(service: FakeService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GKEEP_FORMAT", "text")

    result = runner.invoke(cli.app, ["notes", "list"])

    assert result.exit_code == 0
    assert result.stdout.startswith("1 note(s)")


def test_delete_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_connect", lambda: pytest.fail("must not connect without --confirm"))

    result = runner.invoke(cli.app, ["notes", "delete", "note-1"])

    assert result.exit_code == 1
    assert "confirm" in result.stdout.lower() or "confirm" in result.stderr.lower()


def test_delete_with_confirm(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "delete", "note-1", "--confirm"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["deleted"] == "note-1"


def test_create_list_parses_checked_suffix(service: FakeService) -> None:
    result = runner.invoke(
        cli.app,
        [
            "notes",
            "create-list",
            "--title",
            "Packing",
            "--item",
            "Passport",
            "--item",
            "Charger:checked",
        ],
    )

    assert result.exit_code == 0
    assert service.calls == [
        (
            "create_list_note",
            ([("Passport", False), ("Charger", True)], "Packing"),
            {},
        )
    ]


def test_create_list_requires_items(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["notes", "create-list"])

    assert result.exit_code != 0
    assert "at least one" in result.stdout.lower() or "at least one" in result.stderr.lower()


def test_items_list(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["items", "list", "note-1"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["text"] == "Milk"
    assert service.calls[-1][0] == "list_items"


def test_collaborators_list(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["collaborators", "list", "note-1"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == ["friend@example.com"]
    assert service.calls[-1][0] == "list_collaborators"


def test_labels_list_text(service: FakeService) -> None:
    result = runner.invoke(cli.app, ["labels", "list", "--format", "text"])

    assert result.exit_code == 0
    assert "travel" in result.stdout


def test_auth_show_token_is_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda: Credentials("me@example.com", "aas_et/secret", "keyring"),
    )

    result = runner.invoke(cli.app, ["auth", "show", "--field", "token"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "aas_et/secret"


def test_auth_show_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda: Credentials("me@example.com", "aas_et/secret", "env"),
    )

    result = runner.invoke(cli.app, ["auth", "show"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "email": "me@example.com",
        "master_token": "aas_et/secret",
        "source": "env",
    }


def test_auth_status_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising() -> Credentials:
        raise AuthError("nothing stored")

    monkeypatch.setattr(cli, "load_credentials", raising)

    result = runner.invoke(cli.app, ["auth", "status"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["configured"] is False
    assert "nothing stored" in str(payload["reason"])


def test_auth_status_masks_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda: Credentials("me@example.com", "aas_et/AAAAAAAABBBBCCCC", "keyring"),
    )

    result = runner.invoke(cli.app, ["auth", "status"])

    payload = json.loads(result.stdout)
    assert payload["master_token"] == "aas_...CCCC"
    assert "AAAAAAAABBBB" not in result.stdout


def test_auth_setup_stores_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "stored_credentials", lambda: None)
    monkeypatch.setattr(cli, "exchange_master_token", lambda email, token: "aas_et/new")
    monkeypatch.setattr(cli, "verify_master_token", lambda email, token: None)
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "save_credentials", lambda email, token: saved.append((email, token)))

    result = runner.invoke(
        cli.app,
        [
            "auth",
            "setup",
            "--email",
            "me@example.com",
            "--oauth-token",
            "cookie",
            "--no-browser",
        ],
    )

    assert result.exit_code == 0
    assert saved == [("me@example.com", "aas_et/new")]
    payload = json.loads(result.stdout)
    assert payload["stored"] is True
    assert payload["verified"] is True
