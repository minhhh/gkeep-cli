# gkeep-cli

Command line interface for Google Keep notes, checklists, labels, and
collaborators. Outputs JSON by default, with a colored text mode for humans.
Built on `gkeepapi`, a community client for the private Keep API, because
Google has no official Keep API.

Secrets live in the macOS Keychain (or whatever backend `keyring` finds).
Environment variables override the Keychain, which is what you want on a server.

## Install locally

Install the `gkeep` command into its own environment, linked to this checkout:

```sh
cd gkeep-cli
uv tool install --editable .
```

`--editable` means edits to `src/gkeep_cli/` take effect on the next run, with
no reinstall. `pipx install --editable .` does the same if you prefer pipx.

Check it landed:

```sh
gkeep --version
gkeep --help
```

To remove it again:

```sh
uv tool uninstall gkeep-cli
```

If you only want the command inside the project's own virtualenv, use
`uv pip install --editable .` after `uv sync` (see below).

## Develop locally

Create the virtualenv and install runtime plus dev dependencies (pytest, mypy,
ruff). `uv sync` reads `pyproject.toml`, so it is repeatable on a fresh clone:

```sh
cd gkeep-cli
uv sync
```

That writes `.venv/` and `uv.lock`. Everything below runs through `uv run`, which
uses `.venv` without activating it.

Run the test suite:

```sh
uv run pytest              # all tests
uv run pytest tests/test_service.py -k indent -v
```

Type check and lint:

```sh
uv run mypy                # strict, src and tests
uv run ruff check .
uv run ruff format --check .   # add --diff to see changes
```

To activate the environment interactively instead:

```sh
source .venv/bin/activate
pytest
```

### Run the command from source

Any of these run the working tree, no install step:

```sh
uv run gkeep --help
uv run python -m gkeep_cli notes list --format text
```

The entry point is `gkeep_cli.cli:main`. Commands live in
`src/gkeep_cli/cli.py`, Keep operations in `service.py`, and credential logic in
`auth.py`.

### Try it without a Google account

The service layer accepts an injected `gkeepapi.Keep` stand-in, which is how the
tests run against an in-memory `FakeKeep`. See `tests/test_service.py` for the
shape of a fake note, list, and label.

## First run

```sh
gkeep auth setup
```

The setup command opens Google's embedded sign-in page and asks for the
`oauth_token` cookie. It exchanges that cookie for a master token, verifies the
token against Keep, and stores `{email, masterToken}` in the Keychain under the
service name `gkeep-cli`.

To use the token somewhere else:

```sh
gkeep auth show --field token
```

Or skip the Keychain entirely:

```sh
export GKEEP_EMAIL="you@example.com"
export GKEEP_MASTER_TOKEN="aas_et/..."
```

## Data model

Keep has one top-level object, the note. A note is either a text note or a
checklist. Checklist items live inside their note, so every item command takes
the note ID and items have no account-wide identity. Labels are account-wide and
attached to notes. Collaborators belong to a single note.

| Object | Scope | Commands |
| --- | --- | --- |
| note | account | `notes ...` |
| checklist item | one list note | `items ... <note-id>` |
| label | account, attached to notes | `labels ...` |
| collaborator | one note | `collaborators ... <note-id>` |

JSON follows the same shape. A text note has `"type": "text"` and a `text`
string. A list note has `"type": "list"` and an `items` array. Both carry
`labels` and `collaborators` arrays. `items list` fails on a text note because
there are no items to list.

Converting a text note into a checklist is not exposed, because the private API
has no documented conversion call. Create a note of the other type instead.

## Usage

Output is compact JSON by default. Add `--pretty` to indent it, or
`--format text` (or `GKEEP_FORMAT=text`) for a colored human-readable view.
`--pretty` only affects JSON.

`--limit` and `--offset` paginate the result. `--offset` is applied after
filtering and before `--limit`, so `--offset 40 --limit 20` returns the third
page of 20. Pagination is client-side. `gkeepapi` syncs every note before
`find()` runs, so these flags reduce output size, not network traffic.

Text mode bolds the field lines and prints note content in green. Color is used
only when stdout is a TTY and terminfo reports color support. `NO_COLOR`
disables it, `CLICOLOR=0` disables it, and `FORCE_COLOR=1` forces it when piping
into something like `less -R`. JSON output is never styled.

```sh
gkeep notes list --query shopping --pinned
gkeep notes list --pretty
gkeep notes list --limit 20
gkeep notes list --limit 20 --offset 40
gkeep notes get <note-id>
gkeep notes create "Milk, eggs" --title "Groceries"
gkeep notes create-list --title "Packing" --item Passport --item "Charger:checked"
gkeep notes update <note-id> --color BLUE --add-label travel
gkeep notes delete <note-id> --confirm      # moves to trash
gkeep notes restore <note-id>
gkeep notes purge <note-id> --confirm       # permanent

gkeep items list <note-id>
gkeep items add <note-id> "Water bottle"
gkeep items update <note-id> <item-id> --text "Water bottle" --checked
gkeep items delete <note-id> <item-id>
gkeep items sort <note-id>
gkeep items indent <note-id> <item-id> --parent <parent-item-id>
gkeep items dedent <note-id> <item-id> --parent <parent-item-id>
gkeep items replace <note-id> --item Milk --item "Eggs:checked"

gkeep labels list
gkeep labels create travel
gkeep labels rename travel trips
gkeep labels delete trips --confirm

gkeep collaborators list <note-id>
gkeep collaborators add <note-id> someone@example.com
gkeep collaborators remove <note-id> someone@example.com

gkeep auth status
gkeep auth clear
```

## Command map

| Area | Commands |
| --- | --- |
| auth | `setup`, `show`, `status`, `clear` |
| notes | `list`, `get`, `create`, `create-list`, `update`, `delete`, `restore`, `purge` |
| items | `list`, `add`, `update`, `delete`, `sort`, `indent`, `dedent`, `replace` |
| labels | `list`, `create`, `rename`, `delete` |
| collaborators | `list`, `add`, `remove` |

## How it talks to Google

Google never shipped a public Keep API. There is no documented REST endpoint
for notes, checklists, labels, or collaborators. This tool talks to the same
private endpoints the Android Keep app uses:

- `https://www.googleapis.com/notes/v1/` for note changes, batch mutations,
  and history
- `https://www.googleapis.com/reminders/v1internal/reminders/` for reminders
- `https://keep.google.com/media/v2/` for attachments

Authentication is Google's Android login protocol, implemented by `gpsoauth`,
not standard OAuth 2:

- `gpsoauth` posts to `https://android.clients.google.com/auth` with
  `source=android`, an `androidId`, a `client_sig` value, and a hardcoded Google
  Play Services RSA key (`B64_KEY_7_3_29`).
- It requests the `ac2dm` service to obtain a long-lived token.
- `gkeepapi` exchanges that token for access tokens scoped to `memento` and
  `reminders`, passing `app="com.google.android.keep"` and Keep's client
  signature so Google treats the client as the official Keep app.

So the stack impersonates the Android Keep client. `gpsoauth` imitates Google
Play Services, `gkeepapi` imitates the Keep app, and together they reach an
endpoint that was never meant to be called from outside.

Two token types are involved:

| Stored name | OAuth 2 role | Lifetime | Used for |
| --- | --- | --- | --- |
| `Token` (in the Keychain) | refresh token | long, until revoked | obtaining new access tokens |
| `Auth` (never stored) | access token | hours | `Authorization: OAuth <Auth>` on API calls |

`gkeep-cli` stores only `Token`. `gkeepapi` calls it `master_token` for
historical reasons. The older password login path returned a legacy
`aas_et/...` master token, while the browser setup path returns what `gpsoauth`
documents as an `oauth2rt_1/...` refresh token. Both play the same role.

`gkeepapi` ignores a rotated `Token` if Google returns one during refresh. If
Google ever invalidates the old value when issuing a replacement, the stored
credential stops working and `gkeep auth setup` has to be run again.

## What that means

- Google can change endpoints, client signatures, or auth checks at any time,
  and this tool breaks. That is the cost of an undocumented API.
- The credential is account-wide, not scoped to Keep. It is not a normal OAuth
  grant you can review and revoke per app. Treat the stored value like the
  account password.
- Using it may conflict with Google's terms of service.
- If you need task lists with a supported API, use Google Tasks. It is not Keep
  and has fewer features.
- For data that must stay accessible, export with Google Takeout or move it
  somewhere with a documented API.
