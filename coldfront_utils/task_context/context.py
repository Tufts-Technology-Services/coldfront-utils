"""Who is acting: the user of the web request, or of the request that queued the running task."""

import contextvars
from contextlib import contextmanager

_acting_username = contextvars.ContextVar("acting_username", default=None)

# The keyword that gives the acting user to a task queued where nobody is acting: a scheduled run,
# or a chain's next link, which the cluster's scheduler and monitor queue. The worker takes it out
# of the task's arguments just before calling the function, so the function never sees it; None
# means nobody. A task that declares a parameter of this name keeps it as its own argument.
ACTING_USER_ARGUMENT = "_acting_user"


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
