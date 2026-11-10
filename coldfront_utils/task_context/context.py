"""Who is acting: the user of the web request, or of the request that queued the running task."""

import contextvars
from contextlib import contextmanager

_acting_username = contextvars.ContextVar("acting_username", default=None)
_impersonated_username = contextvars.ContextVar("impersonated_username", default=None)

# The keyword that gives the acting user to a task queued where nobody is acting: a scheduled run,
# or a chain's next link, which the cluster's scheduler and monitor queue. The worker takes it out
# of the task's arguments just before calling the function, so the function never sees it; None
# means nobody. Reserved, as django-q reserves hook, group and timeout: a task's own parameter of
# this name would never receive its argument. @reschedulable refuses a task that has one.
ACTING_USER_ARGUMENT = "_acting_user"


def get_acting_username() -> str | None:
    """The username of whoever is acting, or None when nobody is: a scheduled run, say."""
    return _acting_username.get()


def get_impersonated_username() -> str | None:
    """Whose account the acting user is signed in as, or None when they are signed in as themselves.

    An administrator who has used django-su to sign in as someone is the acting user, and the account
    they are working in is the one this names.
    """
    return _impersonated_username.get()


@contextmanager
def acting_as(username: str | None, *, impersonating: str | None = None):
    """Act as `username` inside the block, and as whoever was acting before once it ends.

    `impersonating` names the account `username` is signed in as, where they used django-su to sign
    in as someone: see get_impersonated_username().

    For code that runs outside a web request on someone's behalf: a management command, a test.
    """
    token = set_acting_username(username)
    impersonating_token = _impersonated_username.set(impersonating)
    try:
        yield
    finally:
        _impersonated_username.reset(impersonating_token)
        reset_acting_username(token)


def set_acting_username(username: str | None) -> contextvars.Token:
    """Act as `username` until the returned token is passed to reset_acting_username."""
    return _acting_username.set(username)


def reset_acting_username(token: contextvars.Token) -> None:
    _acting_username.reset(token)
