"""Carry the acting user from where a task is queued into the worker that runs it.

django-q signals pre_enqueue with the task package just before signing and queuing it, in
whichever process queues it, and pre_execute and post_execute_in_worker around the function in
the worker. The package is a dict that reaches the worker as it was sent, so a key added here
travels with the task.

django-q sends pre_execute and post_execute_in_worker with send(), not send_robust(), so an
exception here would stop the worker. The receivers that run there log their failures instead:
a task that can't be told who's acting still runs.
"""

import inspect
import logging
import pydoc
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.dispatch import receiver
from django_q.signals import post_execute_in_worker, pre_enqueue, pre_execute
from simple_history.models import HistoricalRecords

from .context import ACTING_USER_ARGUMENT, get_acting_username, reset_acting_username, set_acting_username

logger = logging.getLogger(__name__)

# the package key the acting user travels under, and the result key it's reported under
PACKAGE_KEY = "acting_user"

_NOTHING = object()

# Each running task's state, by task id: the token that puts back whoever was acting before it
# ran, and what simple_history's request was before this replaced it (_NOTHING if it didn't).
# Kept per task, not in one slot, because a task can run another inline with async_task(...,
# sync=True): the inner task's receivers run between the outer task's, and must neither overwrite
# nor clear the outer task's state. Each entry is removed when its task has run.
_running_tasks = {}


def _declares_acting_user_argument(func) -> bool:
    """Whether a task function has a parameter named like the keyword, which is then its own.

    `func` is a function or the dotted path django-q stores, resolved the way its worker does.
    """
    if isinstance(func, str):
        func = pydoc.locate(func)
    if not callable(func):
        return False
    try:
        parameter = inspect.signature(func).parameters.get(ACTING_USER_ARGUMENT)
    except (TypeError, ValueError):
        return False
    return parameter is not None and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)


def _given_acting_user(func, arguments):
    """The user a task was given with the _acting_user keyword, or _NOTHING if it wasn't given one.

    A task that declares a parameter of that name wasn't: the argument is its own.
    """
    if not isinstance(arguments, dict) or ACTING_USER_ARGUMENT not in arguments:
        return _NOTHING
    if _declares_acting_user_argument(func):
        return _NOTHING
    return arguments[ACTING_USER_ARGUMENT]


@receiver(pre_enqueue, dispatch_uid="coldfront_utils.task_context.stamp_acting_user")
def stamp_acting_user(sender, task, **kwargs):
    """Put the acting user in the package of a task being queued: None if there's nobody.

    That's the user the task was given with ACTING_USER_ARGUMENT, or else whoever is acting
    where it's queued, which inside a running task is that task's user.
    """
    given = _given_acting_user(task.get("func"), task.get("kwargs"))
    username = get_acting_username() if given is _NOTHING else given
    task[PACKAGE_KEY] = username

    # the monitor queues the next link when this one is done: give each link the user now
    if username is not None and task.get("chain"):
        task["chain"] = [_link_acting_as(link, username) for link in task["chain"]]


def _link_acting_as(link, username):
    """A chain link given the acting user, read the way django-q's async_chain reads it.

    async_chain takes (func, args, kwargs), or a shorter tuple, or a bare function; it checks for
    exactly a tuple, so this does too. A link whose function declares its own _acting_user is
    left as it is.
    """
    if type(link) is not tuple:
        link = (link,)
    link_kwargs = dict(link[2]) if len(link) > 2 else {}
    # the monitor queues each link through here again, by then already carrying the keyword
    if ACTING_USER_ARGUMENT in link_kwargs or _declares_acting_user_argument(link[0]):
        return link
    link_kwargs[ACTING_USER_ARGUMENT] = username
    return (link[0], link[1] if len(link) > 1 else (), link_kwargs)


def _user_named(username):
    """The user whose login name is `username`, looked up the way logging in looks them up."""
    user_model = get_user_model()
    try:
        return user_model._default_manager.get_by_natural_key(username)
    except user_model.DoesNotExist:
        return None


@receiver(pre_execute, dispatch_uid="coldfront_utils.task_context.restore_acting_user")
def restore_acting_user(sender, func, task, **kwargs):
    """Act as the task's user while it runs, for get_acting_username() and for simple_history.

    simple_history takes a history row's user from the web request HistoryRequestMiddleware
    keeps on HistoricalRecords.context, and a worker has none, so every row a task wrote had no
    user. A stand-in request with the acting user's User gives each row they lead to their name.
    """
    # taken out here, not when queued, so it can't reach the function whichever process queued it
    given = _given_acting_user(func, task.get("kwargs"))
    if given is not _NOTHING:
        task["kwargs"].pop(ACTING_USER_ARGUMENT)
        task[PACKAGE_KEY] = given
    username = task.get(PACKAGE_KEY)
    token = set_acting_username(username)
    previous_request = _NOTHING
    try:
        user = _user_named(username) if username else None
        if user is not None:
            previous_request = getattr(HistoricalRecords.context, "request", None)
            HistoricalRecords.context.request = SimpleNamespace(user=user)
    except Exception:
        logger.exception(f"Couldn't give {task.get('name')}'s history rows the acting user {username}")
    _running_tasks[task.get("id")] = (token, previous_request)


@receiver(post_execute_in_worker, dispatch_uid="coldfront_utils.task_context.report_acting_user")
def report_acting_user(sender, func, task, **kwargs):
    """Stop acting as the task's user, and name them in its result if the result is a dict.

    The Task table has no column for it, so this is where the task report shows who queued the
    task. A copy, so a dict the function shares with other code isn't changed.
    """
    state = _running_tasks.pop(task.get("id"), None)
    try:
        if state is not None:
            token, previous_request = state
            reset_acting_username(token)
            if previous_request is None:
                del HistoricalRecords.context.request
            elif previous_request is not _NOTHING:
                HistoricalRecords.context.request = previous_request
    except Exception:
        logger.exception(f"Couldn't stop acting as {task.get(PACKAGE_KEY)} after {task.get('name')}")
    result = task.get("result")
    if task.get("success") and isinstance(result, dict) and PACKAGE_KEY not in result:
        task["result"] = {**result, PACKAGE_KEY: task.get(PACKAGE_KEY)}
