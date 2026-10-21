from __future__ import annotations

import json

import gpsoauth
import keyring
import pytest

from gkeep_cli import auth
from gkeep_cli.auth import Credentials
from gkeep_cli.errors import AuthError


def _set_env(monkeypatch: pytest.MonkeyPatch, email: str | None, token: str | None) -> None:
    for name in auth.EMAIL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in auth.TOKEN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    if email is not None:
        monkeypatch.setenv("GKEEP_EMAIL", email)
    if token is not None:
        monkeypatch.setenv("GKEEP_MASTER_TOKEN", token)


def test_env_wins_over_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, "env@example.com", "env-token")
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, account: pytest.fail("keyring should not be read"),
    )

    credentials = auth.load_credentials()

    assert credentials.email == "env@example.com"
    assert credentials.source == "env"


def test_partial_env_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, "env@example.com", None)

    with pytest.raises(AuthError, match="both"):
        auth.load_credentials()


def test_keyring_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, None, None)
    payload = json.dumps({"email": "stored@example.com", "masterToken": "stored-token"})
    monkeypatch.setattr(keyring, "get_password", lambda service, account: payload)

    credentials = auth.load_credentials()

    assert credentials == Credentials(
        email="stored@example.com", master_token="stored-token", source="keyring"
    )


def test_missing_keyring_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, None, None)
    monkeypatch.setattr(keyring, "get_password", lambda service, account: None)

    with pytest.raises(AuthError, match="auth setup"):
        auth.load_credentials()


def test_malformed_keyring_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, None, None)
    monkeypatch.setattr(keyring, "get_password", lambda service, account: "{oops")

    with pytest.raises(AuthError, match="malformed"):
        auth.load_credentials()


def test_save_credentials_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, None, None)
    store: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, account, value: store.__setitem__((service, account), value),
    )
    monkeypatch.setattr(
        keyring, "get_password", lambda service, account: store.get((service, account))
    )

    auth.save_credentials("me@example.com", "token-123")

    credentials = auth.load_credentials()
    assert credentials.email == "me@example.com"
    assert credentials.master_token == "token-123"


def test_delete_credentials_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising(service: str, account: str) -> None:
        raise keyring.errors.PasswordDeleteError("not found")

    monkeypatch.setattr(keyring, "delete_password", raising)

    assert auth.delete_credentials() is False


@pytest.mark.parametrize(
    "token,expected",
    [("short", "***"), ("aas_et/AAAAAAAABBBBCCCC", "aas_...CCCC")],
)
def test_mask_token(token: str, expected: str) -> None:
    assert auth.mask_token(token) == expected


def test_exchange_master_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpsoauth, "exchange_token", lambda *args: {"Error": "BadAuth"})

    with pytest.raises(AuthError, match="no master token"):
        auth.exchange_master_token("me@example.com", "cookie")


def test_exchange_master_token_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpsoauth, "exchange_token", lambda *args: {"Token": "aas_et/xyz"})

    assert auth.exchange_master_token("me@example.com", "cookie") == "aas_et/xyz"
