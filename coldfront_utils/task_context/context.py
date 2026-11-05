"""Who is acting: the user of the web request, or of the request that queued the running task."""

import contextvars
from contextlib import contextmanager

_acting_username = contextvars.ContextVar("acting_username", default=None)


def get_acting_username() -> str | None:
    """The username of whoever is acting, or None when nobody is: a scheduled run, say."""
    return _acting_username.get()


@contextmanager
def acting_as(username: str | None):
    """Act as `username` inside the block, and as whoever was acting before once it ends.

    For code that runs outside a web request on someone's behalf: a management command, a test.
    """
    token = _acting_username.set(username)
    try:
        yield
    finally:
        _acting_username.reset(token)


def set_acting_username(username: str | None) -> contextvars.Token:
    """Act as `username` until the returned token is passed to reset_acting_username."""
    return _acting_username.set(username)


def reset_acting_username(token: contextvars.Token) -> None:
    _acting_username.reset(token)
