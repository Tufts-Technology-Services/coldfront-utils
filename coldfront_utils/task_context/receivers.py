"""Carry the acting user from where a task is queued into the worker that runs it.

django-q signals pre_enqueue with the task package just before signing and queuing it, in
whichever process queues it, and pre_execute and post_execute_in_worker around the function in
the worker. The package is a dict that reaches the worker as it was sent, so a key added here
travels with the task.

django-q sends pre_execute and post_execute_in_worker with send(), not send_robust(), so an
exception here would stop the worker. The receivers that run there log their failures instead:
a task that can't be told who's acting still runs.
"""

import logging
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.dispatch import receiver
from django_q.signals import post_execute_in_worker, pre_enqueue, pre_execute
from simple_history.models import HistoricalRecords

from .context import get_acting_username, reset_acting_username, set_acting_username

logger = logging.getLogger(__name__)

# the package key the acting user travels under, and the result key it's reported under
PACKAGE_KEY = "acting_user"

_NOTHING = object()

# A worker process runs one task at a time, so it holds at most one task's state: the token that
# puts back whoever was acting before the task ran, and what simple_history's request was before
# this replaced it (_NOTHING if this didn't). None between tasks.
_worker_state = None


@receiver(pre_enqueue, dispatch_uid="coldfront_utils.task_context.stamp_acting_user")
def stamp_acting_user(sender, task, **kwargs):
    """Put whoever is acting where the task is queued in its package: None if nobody is.

    Inside a running task that's the task's own user, so tasks it queues carry them too. A
    chain's next link and a scheduled run, such as an @reschedulable retry, are queued by the
    cluster's monitor and scheduler, where nobody is acting, so they're stamped None.
    """
    task[PACKAGE_KEY] = get_acting_username()


@receiver(pre_execute, dispatch_uid="coldfront_utils.task_context.restore_acting_user")
def restore_acting_user(sender, func, task, **kwargs):
    """Act as the task's user while it runs, for get_acting_username() and for simple_history.

    simple_history takes a history row's user from the web request HistoryRequestMiddleware
    keeps on HistoricalRecords.context, and a worker has none, so every row a task wrote had no
    user. A stand-in request with the acting user's User gives each row they lead to their name.
    """
    global _worker_state
    username = task.get(PACKAGE_KEY)
    token = set_acting_username(username)
    previous_request = _NOTHING
    try:
        user = get_user_model().objects.filter(username=username).first() if username else None
        if user is not None:
            previous_request = getattr(HistoricalRecords.context, "request", None)
            HistoricalRecords.context.request = SimpleNamespace(user=user)
    except Exception:
        logger.exception(f"Couldn't give {task.get('name')}'s history rows the acting user {username}")
    _worker_state = (token, previous_request)


@receiver(post_execute_in_worker, dispatch_uid="coldfront_utils.task_context.report_acting_user")
def report_acting_user(sender, func, task, **kwargs):
    """Stop acting as the task's user, and name them in its result if the result is a dict.

    The Task table has no column for it, so this is where the task report shows who queued the
    task. A copy, so a dict the function shares with other code isn't changed.
    """
    global _worker_state
    try:
        if _worker_state is not None:
            token, previous_request = _worker_state
            reset_acting_username(token)
            if previous_request is None:
                del HistoricalRecords.context.request
            elif previous_request is not _NOTHING:
                HistoricalRecords.context.request = previous_request
    except Exception:
        logger.exception(f"Couldn't stop acting as {task.get(PACKAGE_KEY)} after {task.get('name')}")
    finally:
        _worker_state = None
    result = task.get("result")
    if task.get("success") and isinstance(result, dict) and PACKAGE_KEY not in result:
        task["result"] = {**result, PACKAGE_KEY: task.get(PACKAGE_KEY)}
