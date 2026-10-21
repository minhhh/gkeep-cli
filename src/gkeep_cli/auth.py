"""Credential handling: environment variables, Keychain, master-token exchange."""

from __future__ import annotations

import json
import os
import webbrowser
from dataclasses import dataclass
from typing import Final, Literal

import gpsoauth
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from gkeep_cli.errors import AuthError

KEYCHAIN_SERVICE: Final = "gkeep-cli"
KEYCHAIN_ACCOUNT: Final = "credentials"
ANDROID_ID: Final = "0000000000000000"
EMBEDDED_SETUP_URL: Final = "https://accounts.google.com/EmbeddedSetup"

EMAIL_ENV_VARS: Final = ("GKEEP_EMAIL", "MCP_GOOGLE_KEEP_EMAIL")
TOKEN_ENV_VARS: Final = ("GKEEP_MASTER_TOKEN", "MCP_GOOGLE_KEEP_MASTER_TOKEN")

CredentialSource = Literal["env", "keyring"]


@dataclass(frozen=True)
class Credentials:
    """A Google account email plus the master token that grants Keep access."""

    email: str
    master_token: str
    source: CredentialSource


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def load_credentials() -> Credentials:
    """Resolve credentials from the environment first, then the Keychain."""
    email = _first_env(EMAIL_ENV_VARS)
    token = _first_env(TOKEN_ENV_VARS)

    if email and token:
        return Credentials(email=email, master_token=token, source="env")
    if email or token:
        raise AuthError(
            "Set both the email and the master token environment variables, or neither. "
            f"Email variables: {', '.join(EMAIL_ENV_VARS)}. "
            f"Token variables: {', '.join(TOKEN_ENV_VARS)}."
        )

    stored = stored_credentials()
    if stored is None:
        raise AuthError(
            "No credentials found in the environment or the system keyring. Run: gkeep auth setup"
        )
    return stored


def stored_credentials() -> Credentials | None:
    """Read credentials from the system keyring, if present."""
    try:
        raw = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        raise AuthError(f"Could not read from the system keyring: {exc}") from exc

    if not raw:
        return None

    try:
        payload = json.loads(raw)
        email = payload["email"]
        token = payload["masterToken"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AuthError(
            f"Keyring entry {KEYCHAIN_SERVICE!r}/{KEYCHAIN_ACCOUNT!r} is malformed. "
            "Run: gkeep auth clear && gkeep auth setup"
        ) from exc

    if not isinstance(email, str) or not isinstance(token, str):
        raise AuthError("Keyring entry has a non-string email or master token.")

    return Credentials(email=email, master_token=token, source="keyring")


def save_credentials(email: str, master_token: str) -> None:
    payload = json.dumps({"email": email, "masterToken": master_token})
    try:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, payload)
    except KeyringError as exc:
        raise AuthError(f"Could not write to the system keyring: {exc}") from exc


def delete_credentials() -> bool:
    """Remove the keyring entry. Returns False when it was already absent."""
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except PasswordDeleteError:
        return False
    except KeyringError as exc:
        raise AuthError(f"Could not delete the keyring entry: {exc}") from exc
    return True


def exchange_master_token(email: str, oauth_token: str) -> str:
    """Trade the browser oauth_token cookie for a long-lived master token."""
    try:
        result = gpsoauth.exchange_token(email, oauth_token, ANDROID_ID)
    except Exception as exc:  # gpsoauth raises a shifting set of transport errors
        raise AuthError(f"Master token exchange failed: {exc}") from exc

    token = result.get("Token")
    if not token:
        raise AuthError(f"Google returned no master token: {result}")
    return str(token)


def verify_master_token(email: str, master_token: str) -> None:
    """Open a Keep session and sync once, proving the token still works."""
    import gkeepapi

    keep = gkeepapi.Keep()
    try:
        keep.authenticate(email, master_token)
        keep.sync()
    except Exception as exc:  # gkeepapi wraps transport and API failures loosely
        raise AuthError(f"Google Keep rejected the credentials: {exc}") from exc


def open_setup_page() -> None:
    webbrowser.open(EMBEDDED_SETUP_URL)


def mask_token(token: str) -> str:
    if len(token) <= 12:
        return "***"
    return f"{token[:4]}...{token[-4:]}"
