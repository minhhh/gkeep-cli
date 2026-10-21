"""Typer command line interface for Google Keep."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Annotated

import gkeepapi.exception
import typer

from gkeep_cli import __version__
from gkeep_cli.auth import (
    Credentials,
    delete_credentials,
    exchange_master_token,
    load_credentials,
    mask_token,
    open_setup_page,
    save_credentials,
    stored_credentials,
    verify_master_token,
)
from gkeep_cli.errors import AuthError, GKeepCliError
from gkeep_cli.render import OutputFormat, emit, emit_error
from gkeep_cli.service import KeepService

app = typer.Typer(
    help="Read and write Google Keep notes, checklists, labels, and collaborators.",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
)
auth_app = typer.Typer(help="Manage the Google master token.", no_args_is_help=True)
notes_app = typer.Typer(help="Create, read, update, and trash notes.", no_args_is_help=True)
items_app = typer.Typer(help="Work with checklist items.", no_args_is_help=True)
labels_app = typer.Typer(help="Manage labels.", no_args_is_help=True)
collaborators_app = typer.Typer(
    help="Share notes with other Google accounts.", no_args_is_help=True
)

app.add_typer(auth_app, name="auth")
app.add_typer(notes_app, name="notes")
app.add_typer(items_app, name="items")
app.add_typer(labels_app, name="labels")
app.add_typer(collaborators_app, name="collaborators")

OutputOption = Annotated[
    OutputFormat,
    typer.Option(
        "--format",
        "-f",
        envvar="GKEEP_FORMAT",
        case_sensitive=False,
        help="Output format: json (default) or text.",
    ),
]
PrettyOption = Annotated[
    bool,
    typer.Option("--pretty", help="Indent JSON output instead of the compact default."),
]
NoteArg = Annotated[str, typer.Argument(help="Note ID.")]
ItemArg = Annotated[str, typer.Argument(help="Checklist item ID.")]


class ShowField(StrEnum):
    token = "token"
    email = "email"
    both = "both"


def _connect() -> KeepService:
    """Load credentials and open a Keep session. Tests replace this."""
    credentials = load_credentials()
    return KeepService.connect(credentials.email, credentials.master_token)


def _execute(
    operation: Callable[[KeepService], object],
    output_format: OutputFormat,
    pretty: bool = False,
) -> None:
    try:
        service = _connect()
        data = operation(service)
    except AuthError as exc:
        emit_error(str(exc), output_format, pretty, hint="Run: gkeep auth setup")
        raise typer.Exit(2) from exc
    except GKeepCliError as exc:
        emit_error(str(exc), output_format, pretty)
        raise typer.Exit(1) from exc
    except gkeepapi.exception.KeepException as exc:
        emit_error(f"Google Keep error: {exc}", output_format, pretty)
        raise typer.Exit(1) from exc
    emit(data, output_format, pretty)


def _parse_items(
    raw_items: list[str] | None, option_name: str = "--item"
) -> list[tuple[str, bool]]:
    parsed: list[tuple[str, bool]] = []
    for raw in raw_items or []:
        text = raw.strip()
        checked = False
        if text.endswith(":checked"):
            text = text[: -len(":checked")].rstrip()
            checked = True
        if not text:
            raise typer.BadParameter(f"{option_name} needs non-empty text: {raw!r}")
        parsed.append((text, checked))
    if not parsed:
        raise typer.BadParameter(f"Provide at least one {option_name}")
    return parsed


def _require_confirm(
    confirm: bool, action: str, output_format: OutputFormat, pretty: bool = False
) -> None:
    if not confirm:
        emit_error(f"Refusing to {action} without --confirm", output_format, pretty)
        raise typer.Exit(1)


def _info(message: str) -> None:
    """Progress messages go to stderr so JSON on stdout stays parseable."""
    typer.echo(message, err=True)


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", help="Show the version and exit.", is_eager=True),
    ] = False,
) -> None:
    if version:
        typer.echo(f"gkeep-cli {__version__}")
        raise typer.Exit(0)


# -- auth ------------------------------------------------------------------


@auth_app.command("setup")
def auth_setup(
    email: Annotated[
        str | None, typer.Option("--email", "-e", help="Google account email.")
    ] = None,
    oauth_token: Annotated[
        str | None,
        typer.Option("--oauth-token", help="oauth_token cookie value, skips the prompt."),
    ] = None,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Do not open the sign-in page.")
    ] = False,
    no_verify: Annotated[
        bool, typer.Option("--no-verify", help="Skip the Keep connection check.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing keyring credentials.")
    ] = False,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Obtain a Google master token and store it in the system keychain."""
    existing = None if force else stored_credentials()
    if existing is not None and not typer.confirm(
        f"Credentials for {existing.email} already exist. Overwrite?", default=False
    ):
        _info("Aborted.")
        raise typer.Exit(0)

    account = email or typer.prompt("Google account email")

    if not no_browser:
        _info("")
        _info("1. The sign-in page loads forever. That is normal.")
        _info("2. Open DevTools (F12) -> Application -> Cookies -> accounts.google.com")
        _info("3. Copy the value of the oauth_token cookie.")
        _info("")
        typer.prompt("Press Enter to open the browser", default="", show_default=False)
        open_setup_page()

    token_cookie = oauth_token or typer.prompt("Paste oauth_token", hide_input=True)
    _info("Exchanging oauth_token for a master token...")
    master_token = exchange_master_token(account, token_cookie.strip())
    _info("Master token obtained.")

    if not no_verify:
        _info("Verifying with Google Keep...")
        verify_master_token(account, master_token)
        _info("Google Keep connection verified.")

    save_credentials(account, master_token)

    emit(
        {
            "email": account,
            "stored": True,
            "verified": not no_verify,
            "keyring_service": "gkeep-cli",
        },
        output_format,
        pretty,
    )


@auth_app.command("show")
def auth_show(
    field: Annotated[
        ShowField,
        typer.Option("--field", case_sensitive=False, help="Print one raw value."),
    ] = ShowField.both,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Print stored credentials. Use --field token to capture the raw token."""
    try:
        credentials: Credentials = load_credentials()
    except AuthError as exc:
        emit_error(str(exc), output_format, pretty, hint="Run: gkeep auth setup")
        raise typer.Exit(2) from exc

    if field is ShowField.token:
        typer.echo(credentials.master_token)
        return
    if field is ShowField.email:
        typer.echo(credentials.email)
        return
    emit(
        {
            "email": credentials.email,
            "master_token": credentials.master_token,
            "source": credentials.source,
        },
        output_format,
        pretty,
    )


@auth_app.command("status")
def auth_status(
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Show where credentials come from, with the token masked."""
    try:
        credentials = load_credentials()
    except AuthError as exc:
        emit({"configured": False, "reason": str(exc)}, output_format, pretty)
        return

    emit(
        {
            "configured": True,
            "email": credentials.email,
            "source": credentials.source,
            "master_token": mask_token(credentials.master_token),
        },
        output_format,
        pretty,
    )


@auth_app.command("clear")
def auth_clear(
    force: Annotated[bool, typer.Option("--force", help="Skip the prompt.")] = False,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Delete the keyring entry."""
    if not force and not typer.confirm("Delete stored credentials?", default=False):
        _info("Aborted.")
        raise typer.Exit(0)
    deleted = delete_credentials()
    emit({"deleted": deleted, "keyring_service": "gkeep-cli"}, output_format, pretty)


# -- notes -----------------------------------------------------------------


@notes_app.command("list")
def notes_list(
    query: Annotated[
        str | None, typer.Option("--query", "-q", help="Match title and body text.")
    ] = None,
    label: Annotated[
        list[str] | None, typer.Option("--label", "-l", help="Filter by label name.")
    ] = None,
    color: Annotated[
        list[str] | None, typer.Option("--color", help="Filter by color name.")
    ] = None,
    pinned: Annotated[
        bool | None, typer.Option("--pinned/--no-pinned", help="Filter by pinned state.")
    ] = None,
    archived: Annotated[
        bool | None, typer.Option("--archived/--no-archived", help="Filter by archive state.")
    ] = None,
    trashed: Annotated[bool, typer.Option("--trashed", help="Include trashed notes.")] = False,
    offset: Annotated[
        int,
        typer.Option(
            "--offset",
            min=0,
            help="Skip this many notes before applying --limit.",
        ),
    ] = 0,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", min=1, help="Maximum notes to return."),
    ] = None,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """List notes, optionally filtered and paginated."""
    _execute(
        lambda service: service.list_notes(
            query=query,
            labels=label,
            colors=color,
            pinned=pinned,
            archived=archived,
            trashed=trashed,
            offset=offset,
            limit=limit,
        ),
        output_format,
        pretty,
    )


@notes_app.command("get")
def notes_get(
    note_id: NoteArg,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Fetch one note by ID."""
    _execute(lambda service: service.get_note(note_id), output_format, pretty)


@notes_app.command("create")
def notes_create(
    text: Annotated[str, typer.Argument(help="Note body.")],
    title: Annotated[str, typer.Option("--title", "-t", help="Note title.")] = "",
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Create a text note."""
    _execute(lambda service: service.create_text_note(text, title), output_format, pretty)


@notes_app.command("create-list")
def notes_create_list(
    title: Annotated[str, typer.Option("--title", "-t", help="Note title.")] = "",
    item: Annotated[
        list[str] | None,
        typer.Option("--item", "-i", help="Item text, append ':checked' to pre-check it."),
    ] = None,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Create a checklist note."""
    items = _parse_items(item)
    _execute(lambda service: service.create_list_note(items, title), output_format, pretty)


@notes_app.command("update")
def notes_update(
    note_id: NoteArg,
    title: Annotated[str | None, typer.Option("--title", "-t")] = None,
    text: Annotated[str | None, typer.Option("--text")] = None,
    pinned: Annotated[bool | None, typer.Option("--pinned/--no-pinned")] = None,
    archived: Annotated[bool | None, typer.Option("--archived/--no-archived")] = None,
    color: Annotated[
        str | None,
        typer.Option("--color", help="Color name, e.g. RED or BLUE."),
    ] = None,
    add_label: Annotated[list[str] | None, typer.Option("--add-label")] = None,
    remove_label: Annotated[list[str] | None, typer.Option("--remove-label")] = None,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Update a note's title, body, color, labels, or state."""
    _execute(
        lambda service: service.update_note(
            note_id,
            title=title,
            text=text,
            pinned=pinned,
            archived=archived,
            color=color,
            add_labels=add_label,
            remove_labels=remove_label,
        ),
        output_format,
        pretty,
    )


@notes_app.command("delete")
def notes_delete(
    note_id: NoteArg,
    confirm: Annotated[bool, typer.Option("--confirm", help="Required.")] = False,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Move a note to the trash."""
    _require_confirm(confirm, "delete this note", output_format, pretty)
    _execute(lambda service: service.delete_note(note_id), output_format, pretty)


@notes_app.command("restore")
def notes_restore(
    note_id: NoteArg,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Restore a note from the trash."""
    _execute(lambda service: service.restore_note(note_id), output_format, pretty)


@notes_app.command("purge")
def notes_purge(
    note_id: NoteArg,
    confirm: Annotated[bool, typer.Option("--confirm", help="Required.")] = False,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Delete a note permanently. There is no undo."""
    _require_confirm(confirm, "permanently delete this note", output_format, pretty)
    _execute(lambda service: service.purge_note(note_id), output_format, pretty)


# -- checklist items -------------------------------------------------------


@items_app.command("list")
def items_list(
    note_id: NoteArg,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """List the items in a checklist."""
    _execute(lambda service: service.list_items(note_id), output_format, pretty)


@items_app.command("add")
def items_add(
    note_id: NoteArg,
    text: Annotated[str, typer.Argument(help="Item text.")],
    checked: Annotated[bool, typer.Option("--checked", help="Create it checked.")] = False,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Append an item to a checklist."""
    _execute(lambda service: service.add_list_item(note_id, text, checked), output_format, pretty)


@items_app.command("update")
def items_update(
    note_id: NoteArg,
    item_id: ItemArg,
    text: Annotated[str | None, typer.Option("--text")] = None,
    checked: Annotated[bool | None, typer.Option("--checked/--unchecked")] = None,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Change an item's text or checked state."""
    _execute(
        lambda service: service.update_list_item(note_id, item_id, text=text, checked=checked),
        output_format,
        pretty,
    )


@items_app.command("delete")
def items_delete(
    note_id: NoteArg,
    item_id: ItemArg,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Delete an item from a checklist."""
    _execute(lambda service: service.delete_list_item(note_id, item_id), output_format, pretty)


@items_app.command("sort")
def items_sort(
    note_id: NoteArg,
    reverse: Annotated[bool, typer.Option("--reverse", help="Sort descending.")] = False,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Sort checklist items alphabetically."""
    _execute(lambda service: service.sort_list_items(note_id, reverse), output_format, pretty)


@items_app.command("indent")
def items_indent(
    note_id: NoteArg,
    item_id: ItemArg,
    parent: Annotated[str, typer.Option("--parent", help="Parent item ID.")],
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Nest an item under a parent item."""
    _execute(
        lambda service: service.indent_list_item(note_id, item_id, parent), output_format, pretty
    )


@items_app.command("dedent")
def items_dedent(
    note_id: NoteArg,
    item_id: ItemArg,
    parent: Annotated[str, typer.Option("--parent", help="Parent item ID.")],
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Remove one level of nesting from an item."""
    _execute(
        lambda service: service.dedent_list_item(note_id, item_id, parent), output_format, pretty
    )


@items_app.command("replace")
def items_replace(
    note_id: NoteArg,
    item: Annotated[
        list[str] | None,
        typer.Option("--item", "-i", help="Item text, append ':checked' to pre-check it."),
    ] = None,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Replace every item in a checklist."""
    items = _parse_items(item)
    _execute(lambda service: service.replace_list_items(note_id, items), output_format, pretty)


# -- labels ----------------------------------------------------------------


@labels_app.command("list")
def labels_list(
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """List labels."""
    _execute(lambda service: service.list_labels(), output_format, pretty)


@labels_app.command("create")
def labels_create(
    name: Annotated[str, typer.Argument(help="Label name.")],
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Create a label."""
    _execute(lambda service: service.create_label(name), output_format, pretty)


@labels_app.command("rename")
def labels_rename(
    name: Annotated[str, typer.Argument(help="Current label name.")],
    new_name: Annotated[str, typer.Argument(help="New label name.")],
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Rename a label."""
    _execute(lambda service: service.rename_label(name, new_name), output_format, pretty)


@labels_app.command("delete")
def labels_delete(
    name: Annotated[str, typer.Argument(help="Label name.")],
    confirm: Annotated[bool, typer.Option("--confirm", help="Required.")] = False,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Delete a label and remove it from every note."""
    _require_confirm(confirm, "delete this label", output_format, pretty)
    _execute(lambda service: service.delete_label(name), output_format, pretty)


# -- collaborators ---------------------------------------------------------


@collaborators_app.command("list")
def collaborators_list(
    note_id: NoteArg,
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """List the accounts a note is shared with."""
    _execute(lambda service: service.list_collaborators(note_id), output_format, pretty)


@collaborators_app.command("add")
def collaborators_add(
    note_id: NoteArg,
    email: Annotated[str, typer.Argument(help="Google account email.")],
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Share a note with another account."""
    _execute(lambda service: service.add_collaborator(note_id, email), output_format, pretty)


@collaborators_app.command("remove")
def collaborators_remove(
    note_id: NoteArg,
    email: Annotated[str, typer.Argument(help="Google account email.")],
    pretty: PrettyOption = False,
    output_format: OutputOption = OutputFormat.json,
) -> None:
    """Revoke another account's access to a note."""
    _execute(lambda service: service.remove_collaborator(note_id, email), output_format, pretty)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
