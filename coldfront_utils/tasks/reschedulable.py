"""Tasks that wait for something by scheduling themselves to run again later.

For waits measured in minutes, such as a new group reaching Active Directory or a new share
mounting on a cluster. django-q2's own retry doesn't fit those: it redelivers a task that failed,
on brokers that track acknowledgement only (not Redis), after one cluster-wide delay, with the same
arguments, whatever the failure was. Retrying inside the run would hold a worker past the task
timeout.
"""

import ast
import functools
import inspect
import logging
from datetime import timedelta

from django.utils import timezone
from django_q.tasks import Schedule, schedule

from .results import SCHEDULED, build_task_result

logger = logging.getLogger(__name__)

# The keyword arguments the decorator adds to every call, carrying the retry budget between runs.
RETRY_ARGUMENTS = frozenset({"retries", "wait"})

# Keyword arguments django-q2 takes for itself: schedule() pops these when it stores a schedule,
# and async_task() pops these when the schedule runs. A task parameter with one of these names would
# be lost, or taken as a queue option, on every retry.
#
# "group" isn't one: async_task() only takes an option from the arguments when q_options doesn't
# have it, and the scheduler always puts a "group" into a schedule's q_options. "cluster" is in
# q_options too, but schedule() has already taken it from the arguments by then.
DJANGO_Q_ARGUMENTS = frozenset(
    {
        # schedule()
        "name",
        "hook",
        "schedule_type",
        "minutes",
        "repeats",
        "next_run",
        "cron",
        "cluster",
        "intended_date_kwarg",
        # async_task()
        "q_options",
        "task_name",
        "save",
        "sync",
        "cached",
        "ack_failure",
        "iter_count",
        "iter_cached",
        "chain",
        "broker",
        "timeout",
    }
)


class RetryLater(Exception):
    """Raised by a task body to have it run again later.

    `reason` says what the task is waiting for. `changes` replace the named arguments for the next
    run, so a task can carry progress into it: skip a step that is done, or pass on an id it was
    given. Raise it from the exception that caused the wait (`raise RetryLater(...) from error`):
    once the retries run out, that exception is the one the task fails with.
    """

    def __init__(self, reason: str, **changes):
        super().__init__(reason)
        self.reason = reason
        self.changes = changes


class Rescheduled(dict):
    """What a run returns once it has scheduled its retry.

    A dict, so django-q stores it and the task report shows it like any other result: its "message"
    is "scheduled:<reason> (runs again in ...)". It also has "reason", "retries_left" and
    "next_run". A decorator that wraps a reschedulable task can tell it apart from a finished run
    with isinstance(), to record that the task is waiting rather than done.
    """


def reschedulable(*, retry_on: tuple = (), retries: int = 5, wait: int = 5, q_options=None):
    """Decorate a django-q task so that it runs again later when it can't finish yet.

    The task runs again, `wait` minutes later and at most `retries` times, when its body raises
    RetryLater or one of the `retry_on` exception types. Anything else it raises propagates as it
    would without the decorator. Once the retries are used up, the error that would have caused the
    next retry is raised, so the task fails with its own reason: a `retry_on` error as it was, and a
    RetryLater as the exception it was raised from, or as itself if it wasn't raised from one.

    The retry calls the same task with the same arguments, apart from any a RetryLater changes. The
    decorator rebuilds them from the call, so a task doesn't list them again. They are passed by
    keyword and stored by django-q as text, which it reads back with ast.literal_eval. So every
    argument has to be a literal: a string, number, boolean, None, or a list, tuple, set or dict of
    those. A Path or a model instance isn't, and is rejected when the retry is scheduled rather than
    breaking the retry when it runs.

    Each call takes two more keyword arguments, `retries` and `wait`, which default to the
    decorator's and are passed on to the next run with one retry fewer. So the task can't have
    parameters of those names, or of the names django-q2 takes for itself (DJANGO_Q_ARGUMENTS).
    Every parameter has to be one a keyword can fill: no positional-only parameters or *args.

    `q_options` are passed to django-q for the retry, such as a "task_name" or "group". Give a dict,
    or a function that takes the task's arguments as keywords and returns one.

    A run that schedules a retry returns a Rescheduled result instead of raising, so django-q
    records it as a successful run. The task has to be importable by its dotted path, because that
    is how the retry calls it: define it at module level, not inside a function.
    """
    retry_on = tuple(retry_on)
    if any(not (isinstance(kind, type) and issubclass(kind, BaseException)) for kind in retry_on):
        raise TypeError(f"retry_on must be exception types, not {retry_on!r}")

    def decorate(func):
        signature = inspect.signature(func)
        task_path = f"{func.__module__}.{func.__qualname__}"
        _check_task_can_be_rescheduled(func, signature, task_path)
        accepts_any_keyword = any(p.kind is p.VAR_KEYWORD for p in signature.parameters.values())

        @functools.wraps(func)
        def wrapper(*args, retries: int = retries, wait: int = wait, **kwargs):
            try:
                return func(*args, **kwargs)
            except RetryLater as raised:
                error, reason, changes = raised, raised.reason, raised.changes
            except retry_on as raised:
                error, reason, changes = raised, str(raised), {}

            if retries <= 0:
                logger.error(f"{task_path} has no retries left: {reason}")
                # a RetryLater raised from the real error gives way to it, so the task fails with that
                cause = error.__cause__ if isinstance(error, RetryLater) else None
                raise (cause if isinstance(cause, Exception) else error)

            next_arguments = {**_arguments_by_keyword(signature, args, kwargs), **changes}
            reserved = sorted((RETRY_ARGUMENTS | DJANGO_Q_ARGUMENTS) & set(changes))
            if reserved:
                raise TypeError(f"RetryLater can't change {', '.join(reserved)}: the retry needs them for itself")
            if not accepts_any_keyword:
                unknown = sorted(set(changes) - set(signature.parameters))
                if unknown:
                    raise TypeError(f"RetryLater changes arguments {task_path} doesn't take: {', '.join(unknown)}")
            options = q_options(**next_arguments) if callable(q_options) else dict(q_options or {})
            _check_literal(task_path, {**next_arguments, "q_options": options})

            next_run = timezone.now() + timedelta(minutes=wait)
            logger.warning(
                f"{task_path} will run again in {wait} minutes ({retries - 1} retries left after that): {reason}"
            )
            schedule(
                task_path,
                **next_arguments,
                retries=retries - 1,
                wait=wait,
                q_options=options,
                schedule_type=Schedule.ONCE,
                next_run=next_run,
            )
            result = build_task_result(
                SCHEDULED, f"{reason} (runs again in {wait} minutes, {retries - 1} retries left)"
            )
            return Rescheduled(result, reason=reason, retries_left=retries - 1, next_run=next_run.isoformat())

        return wrapper

    return decorate


def _check_task_can_be_rescheduled(func, signature, task_path) -> None:
    """Refuse, when the module loads, a task whose retry couldn't call it the way it was called."""
    if "<locals>" in func.__qualname__:
        raise TypeError(f"{task_path} must be defined at module level, so its retry can import it by path")
    for parameter in signature.parameters.values():
        if parameter.kind is parameter.POSITIONAL_ONLY:
            raise TypeError(
                f"{task_path}: the retry passes arguments by keyword, so '{parameter.name}' can't be positional-only"
            )
        if parameter.kind is parameter.VAR_POSITIONAL:
            raise TypeError(f"{task_path}: the retry passes arguments by keyword, so it can't take *{parameter.name}")
    taken = sorted((RETRY_ARGUMENTS | DJANGO_Q_ARGUMENTS) & set(signature.parameters))
    if taken:
        raise TypeError(f"{task_path} has parameters the retry needs for itself: {', '.join(taken)}")


def _arguments_by_keyword(signature, args, kwargs) -> dict:
    """The arguments a call was given, by parameter name, with any **kwargs spread back out."""
    bound = signature.bind(*args, **kwargs)
    by_keyword = {}
    for name, value in bound.arguments.items():
        if signature.parameters[name].kind is inspect.Parameter.VAR_KEYWORD:
            by_keyword.update(value)
        else:
            by_keyword[name] = value
    return by_keyword


def _check_literal(task_path, arguments: dict) -> None:
    """Refuse arguments django-q couldn't read back: it would run the retry without any of them."""
    unreadable = []
    for name, value in arguments.items():
        try:
            readable = ast.literal_eval(repr(value)) == value
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            readable = False
        if not readable:
            unreadable.append(f"{name}={value!r}")
    if unreadable:
        raise TypeError(
            f"{task_path} can't be scheduled to run again: django-q stores arguments as text and reads them "
            f"back with ast.literal_eval, which can't read {', '.join(unreadable)}"
        )
