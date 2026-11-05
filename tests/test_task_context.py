"""task_context: who is acting in a web request, and the same user in every task it leads to.

The tasks run through django-q's sync mode, which signals pre_enqueue, pre_execute and
post_execute_in_worker just as the cluster does, but runs the worker in this process. So a test
that wants to see the worker take its user from the package, not from this process, gives the
package a different user from whoever is acting here.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from coldfront.core.project.models import Project
from coldfront.core.test_helpers.factories import ProjectFactory
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django.utils import timezone
from django_q.conf import Conf
from django_q.models import Schedule, Task
from django_q.scheduler import scheduler
from django_q.signals import pre_enqueue
from django_q.tasks import async_chain, async_task
from simple_history.models import HistoricalRecords

from coldfront_utils.task_context import ACTING_USER_ARGUMENT, acting_as, get_acting_username
from coldfront_utils.task_context.middleware import ActingUserMiddleware
from coldfront_utils.task_context.receivers import report_acting_user
from coldfront_utils.tasks import RetryLater, reschedulable

LAB_SUMMARY = {"directory": "desjardinslab"}


def return_lab_summary():
    """A task whose result is a dict, and one other code shares."""
    return LAB_SUMMARY


def return_lab_name():
    return "desjardinslab"


def report_who_is_acting():
    return {"seen": get_acting_username()}


def queue_lab_summary():
    """A task that queues another, as the storage plugin's dispatchers do."""
    return {"queued": async_task("tests.test_task_context.return_lab_summary", sync=True)}


# who each run of wait_for_lab_volume acted for
runs_acted_for = []


@reschedulable(retries=2)
def wait_for_lab_volume(volume):
    """Waits once, as a task waiting for Starfish to index a volume would."""
    runs_acted_for.append(get_acting_username())
    if len(runs_acted_for) == 1:
        raise RetryLater(f"{volume} not indexed yet")
    return {"volume": volume}


def queue_then_report():
    """A task that runs another inline, then says who it's acting for and who history would name."""
    async_task("tests.test_task_context.report_who_is_acting", sync=True)
    history_request = getattr(HistoricalRecords.context, "request", None)
    return {"seen_after": get_acting_username(), "history_user": history_request and history_request.user.username}


def keep_its_own_acting_user(_acting_user=None):
    """A task with its own parameter of the reserved name: it's the task's, not the acting user."""
    return {"argument": _acting_user, "seen": get_acting_username()}


def retitle_project(project_pk):
    """A task that changes a model simple_history records."""
    project = Project.objects.get(pk=project_pk)
    project.title = "Retitled by a task"
    project.save()


def request_from(user):
    request = RequestFactory().get("/allocation/2330/")
    request.user = user
    return request


# --- in the web process ------------------------------------------------------------------------


def test_the_request_user_is_acting_while_the_request_is_handled():
    seen = []
    middleware = ActingUserMiddleware(lambda request: seen.append(get_acting_username()))

    middleware(request_from(User(username="tphimm01")))

    assert seen == ["tphimm01"]
    assert get_acting_username() is None


def test_nobody_is_acting_for_an_anonymous_request():
    seen = []
    middleware = ActingUserMiddleware(lambda request: seen.append(get_acting_username()))

    middleware(request_from(AnonymousUser()))

    assert seen == [None]


def test_the_user_stops_acting_even_when_the_view_raises():
    def failing_view(request):
        raise RuntimeError("the view broke")

    with pytest.raises(RuntimeError):
        ActingUserMiddleware(failing_view)(request_from(User(username="tphimm01")))

    assert get_acting_username() is None


def test_acting_as_puts_back_whoever_was_acting_before():
    with acting_as("tphimm01"):
        with acting_as("cbarne02"):
            assert get_acting_username() == "cbarne02"
        assert get_acting_username() == "tphimm01"
    assert get_acting_username() is None


# --- when a task is queued ---------------------------------------------------------------------


@pytest.fixture
def packages():
    """The packages of the tasks queued during a test, as the pre_enqueue receivers left them."""
    seen = []

    def record_package(sender, task, **kwargs):
        seen.append(dict(task))

    # connected after the app's own receiver, so it sees the package that receiver stamped
    pre_enqueue.connect(record_package, dispatch_uid="test-record-package")
    yield seen
    pre_enqueue.disconnect(dispatch_uid="test-record-package")


@pytest.mark.django_db
def test_a_task_queued_while_someone_acts_carries_them(packages):
    with acting_as("tphimm01"):
        task_id = async_task("tests.test_task_context.return_lab_summary", sync=True)

    assert packages[0]["acting_user"] == "tphimm01"
    assert Task.objects.get(id=task_id).result == {"directory": "desjardinslab", "acting_user": "tphimm01"}


@pytest.mark.django_db
def test_a_task_queued_with_nobody_acting_says_so(packages):
    """A scheduled run, say: its result names nobody, which is itself worth seeing."""
    task_id = async_task("tests.test_task_context.return_lab_summary", sync=True)

    assert packages[0]["acting_user"] is None
    assert Task.objects.get(id=task_id).result == {"directory": "desjardinslab", "acting_user": None}


@pytest.mark.django_db
def test_a_result_that_isnt_a_dict_is_left_as_it_is():
    with acting_as("tphimm01"):
        task_id = async_task("tests.test_task_context.return_lab_name", sync=True)

    assert Task.objects.get(id=task_id).result == "desjardinslab"


@pytest.mark.django_db
def test_a_dict_the_task_shares_isnt_changed():
    with acting_as("tphimm01"):
        async_task("tests.test_task_context.return_lab_summary", sync=True)

    assert LAB_SUMMARY == {"directory": "desjardinslab"}


@pytest.mark.parametrize(
    "success, result",
    [
        pytest.param(False, "ValueError: no quota", id="a-failed-tasks-traceback"),
        pytest.param(True, {"acting_user": "cbarne02"}, id="a-result-that-already-names-an-acting-user"),
    ],
)
def test_a_result_is_left_as_it_is(success, result):
    package = {"success": success, "result": result, "acting_user": "tphimm01"}

    report_acting_user(sender="django_q", func=None, task=package)

    assert package["result"] == result


# --- while a task runs -------------------------------------------------------------------------


@pytest.fixture
def package_user():
    """Give queued tasks a chosen user in their package, whoever is acting where they're queued.

    For the one test of the package alone; the others give tasks a user with the keyword.
    """
    chosen = {}

    def override_package_user(sender, task, **kwargs):
        if "username" in chosen:
            task["acting_user"] = chosen["username"]

    pre_enqueue.connect(override_package_user, dispatch_uid="test-override-package-user")
    yield chosen
    pre_enqueue.disconnect(dispatch_uid="test-override-package-user")


@pytest.mark.django_db
def test_a_task_acts_as_the_user_in_its_package(package_user):
    package_user["username"] = "tphimm01"

    with acting_as("cbarne02"):
        task_id = async_task("tests.test_task_context.report_who_is_acting", sync=True)
        # and whoever was acting before is acting again once the task is done
        assert get_acting_username() == "cbarne02"

    assert Task.objects.get(id=task_id).result == {"seen": "tphimm01", "acting_user": "tphimm01"}


@pytest.mark.django_db
def test_a_task_queued_by_a_task_carries_its_user(packages):
    with acting_as("tphimm01"):
        async_task("tests.test_task_context.queue_lab_summary", sync=True)

    assert [package["acting_user"] for package in packages] == ["tphimm01", "tphimm01"]


@pytest.mark.django_db
def test_history_rows_a_task_writes_name_its_user():
    """Without this, every row a task wrote had no user: the worker has no request."""
    user = User.objects.create(username="tphimm01")
    project = ProjectFactory()

    async_task("tests.test_task_context.retitle_project", project.pk, sync=True, **{ACTING_USER_ARGUMENT: "tphimm01"})

    latest = Project.history.filter(id=project.pk).latest("history_date")
    assert latest.title == "Retitled by a task"
    assert latest.history_user == user
    assert not hasattr(HistoricalRecords.context, "request"), "the stand-in request must not outlive the task"


@pytest.mark.django_db
def test_a_user_with_no_account_here_leaves_history_rows_without_one():
    project = ProjectFactory()

    async_task("tests.test_task_context.retitle_project", project.pk, sync=True, **{ACTING_USER_ARGUMENT: "nobody01"})

    assert Project.history.filter(id=project.pk).latest("history_date").history_user is None


@pytest.mark.django_db
def test_a_task_still_runs_when_its_user_cant_be_looked_up():
    """django-q sends these signals without catching what receivers raise: it would stop the worker."""
    with mock.patch("coldfront_utils.task_context.receivers.get_user_model", side_effect=RuntimeError("no db")):
        task_id = async_task(
            "tests.test_task_context.report_who_is_acting", sync=True, **{ACTING_USER_ARGUMENT: "tphimm01"}
        )

    assert Task.objects.get(id=task_id).result == {"seen": "tphimm01", "acting_user": "tphimm01"}
    assert get_acting_username() is None


@pytest.mark.django_db
def test_a_request_simple_history_already_had_is_put_back():
    """Sync mode runs the task inside the caller's process, which may be handling a request."""
    User.objects.create(username="tphimm01")
    web_request = SimpleNamespace(user=AnonymousUser())
    HistoricalRecords.context.request = web_request
    try:
        async_task("tests.test_task_context.report_who_is_acting", sync=True, **{ACTING_USER_ARGUMENT: "tphimm01"})

        assert HistoricalRecords.context.request is web_request
    finally:
        del HistoricalRecords.context.request


# --- tasks the cluster queues itself -----------------------------------------------------------


@pytest.fixture
def fresh_runs():
    runs_acted_for.clear()
    yield runs_acted_for
    runs_acted_for.clear()


@pytest.mark.django_db
def test_a_retry_runs_for_whoever_the_first_run_ran_for(fresh_runs):
    """django-q's scheduler queues the retry with nobody acting: the retry is told who."""
    with acting_as("tphimm01"):
        wait_for_lab_volume("vol1")

    retry = Schedule.objects.get()
    assert f"'{ACTING_USER_ARGUMENT}': 'tphimm01'" in retry.kwargs
    Schedule.objects.update(next_run=timezone.now() - timedelta(minutes=1))
    with mock.patch.object(Conf, "SYNC", True):
        scheduler()

    assert fresh_runs == ["tphimm01", "tphimm01"]
    assert Task.objects.get(func="tests.test_task_context.wait_for_lab_volume").result == {
        "volume": "vol1",
        "acting_user": "tphimm01",
    }


@pytest.mark.django_db
def test_a_retry_with_nobody_acting_isnt_given_anyone(fresh_runs):
    wait_for_lab_volume("vol1")

    assert ACTING_USER_ARGUMENT not in Schedule.objects.get().kwargs


@pytest.mark.django_db
def test_without_task_context_a_retry_isnt_given_the_keyword(fresh_runs):
    """Nothing would take it out again, so the retry would fail with an unexpected keyword."""
    with acting_as("tphimm01"), mock.patch("coldfront_utils.tasks.reschedulable.apps.is_installed", return_value=False):
        wait_for_lab_volume("vol1")

    assert ACTING_USER_ARGUMENT not in Schedule.objects.get().kwargs


@pytest.mark.django_db
def test_the_keyword_reaches_the_package_but_not_the_function():
    """report_who_is_acting takes no arguments, so it would fail if the keyword reached it."""
    task_id = async_task(
        "tests.test_task_context.report_who_is_acting", sync=True, **{ACTING_USER_ARGUMENT: "tphimm01"}
    )

    assert Task.objects.get(id=task_id).result == {"seen": "tphimm01", "acting_user": "tphimm01"}


@pytest.mark.django_db
def test_a_chains_later_links_run_for_its_user():
    """django-q's monitor queues each next link with nobody acting: the chain gives them the user.

    Started with the keyword rather than inside acting_as, so the later link's user can only come
    from the chain: in sync mode the monitor runs in this process.
    """
    async_chain(
        [
            ("tests.test_task_context.report_who_is_acting", (), {ACTING_USER_ARGUMENT: "tphimm01"}),
            ("tests.test_task_context.report_who_is_acting",),
        ],
        sync=True,
    )

    results = [task.result for task in Task.objects.order_by("started")]
    assert results == [{"seen": "tphimm01", "acting_user": "tphimm01"}] * 2


# --- a task that runs another inline -----------------------------------------------------------


@pytest.mark.django_db
def test_a_task_that_runs_another_inline_keeps_its_own_user():
    """The inner task's receivers run between the outer task's, and must leave its state alone."""
    User.objects.create(username="tphimm01")

    task_id = async_task("tests.test_task_context.queue_then_report", sync=True, **{ACTING_USER_ARGUMENT: "tphimm01"})

    assert Task.objects.get(id=task_id).result == {
        "seen_after": "tphimm01",
        "history_user": "tphimm01",
        "acting_user": "tphimm01",
    }
    # and once the outer task is done, nothing it set is left behind for the next task
    assert get_acting_username() is None
    assert not hasattr(HistoricalRecords.context, "request")


# --- the _acting_user keyword --------------------------------------------------------------------


@pytest.mark.django_db
def test_an_explicit_none_means_nobody_even_while_someone_acts():
    with acting_as("tphimm01"):
        task_id = async_task("tests.test_task_context.report_who_is_acting", sync=True, **{ACTING_USER_ARGUMENT: None})

    assert Task.objects.get(id=task_id).result == {"seen": None, "acting_user": None}


@pytest.mark.django_db
def test_a_task_that_declares_the_parameter_keeps_its_argument():
    task_id = async_task("tests.test_task_context.keep_its_own_acting_user", sync=True, **{ACTING_USER_ARGUMENT: "bob"})

    assert Task.objects.get(id=task_id).result == {"argument": "bob", "seen": None, "acting_user": None}


@pytest.mark.django_db
def test_the_worker_takes_the_keyword_out_of_a_task_queued_without_the_receiver():
    """As a scheduler or monitor still running older code would queue it, during a deploy."""
    from django_q.humanhash import uuid
    from django_q.signing import SignedPackage
    from django_q.tasks import _sync

    tag = uuid()
    package = SignedPackage.dumps(
        {
            "id": tag[1],
            "name": tag[0],
            "func": "tests.test_task_context.report_who_is_acting",
            "args": (),
            "kwargs": {ACTING_USER_ARGUMENT: "tphimm01"},
            "started": timezone.now(),
        }
    )

    task = Task.objects.get(id=_sync(package))

    assert task.success is True
    assert task.result == {"seen": "tphimm01", "acting_user": "tphimm01"}
