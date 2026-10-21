"""Exception types shared by the auth, service, and CLI layers."""

from __future__ import annotations


class GKeepCliError(RuntimeError):
    """Base class for errors this tool reports to the user."""


class AuthError(GKeepCliError):
    """Missing, malformed, or rejected credentials."""


class KeepError(GKeepCliError):
    """A Google Keep operation failed."""


class NoteNotFound(KeepError):
    pass


class LabelNotFound(KeepError):
    pass


class NotAListNote(KeepError):
    pass


class ItemNotFound(KeepError):
    pass
