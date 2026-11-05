"""task_context: who is acting in a web request, and the same user in every task it leads to.

The tasks run through django-q's sync mode, which signals pre_enqueue, pre_execute and
post_execute_in_worker just as the cluster does, but runs the worker in this process. So a test
that wants to see the worker take its user from the package, not from this process, gives the
package a different user from whoever is acting here.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
from coldfront.core.project.models import Project
from coldfront.core.test_helpers.factories import ProjectFactory
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django_q.models import Task
from django_q.signals import pre_enqueue
from django_q.tasks import async_task
from simple_history.models import HistoricalRecords

from coldfront_utils.task_context import acting_as, get_acting_username
from coldfront_utils.task_context.middleware import ActingUserMiddleware
from coldfront_utils.task_context.receivers import report_acting_user

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

    middleware(request_from(SimpleNamespace(is_authenticated=True, username="tphimm01")))

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
        ActingUserMiddleware(failing_view)(request_from(SimpleNamespace(is_authenticated=True, username="tphimm01")))

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


def test_a_failed_tasks_traceback_is_left_as_it_is():
    package = {"success": False, "result": "ValueError: no quota", "acting_user": "tphimm01"}

    report_acting_user(sender="django_q", func=None, task=package)

    assert package["result"] == "ValueError: no quota"


def test_a_result_that_already_names_an_acting_user_keeps_it():
    package = {"success": True, "result": {"acting_user": "cbarne02"}, "acting_user": "tphimm01"}

    report_acting_user(sender="django_q", func=None, task=package)

    assert package["result"] == {"acting_user": "cbarne02"}


# --- while a task runs -------------------------------------------------------------------------


@pytest.fixture
def package_user():
    """Give queued tasks a chosen user in their package, whoever is acting where they're queued."""
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
def test_history_rows_a_task_writes_name_its_user(package_user):
    """Without this, every row a task wrote had no user: the worker has no request."""
    user = User.objects.create(username="tphimm01")
    project = ProjectFactory()
    package_user["username"] = "tphimm01"

    async_task("tests.test_task_context.retitle_project", project.pk, sync=True)

    latest = Project.history.filter(id=project.pk).latest("history_date")
    assert latest.title == "Retitled by a task"
    assert latest.history_user == user
    assert not hasattr(HistoricalRecords.context, "request"), "the stand-in request must not outlive the task"


@pytest.mark.django_db
def test_a_user_with_no_account_here_leaves_history_rows_without_one(package_user):
    project = ProjectFactory()
    package_user["username"] = "nobody01"

    async_task("tests.test_task_context.retitle_project", project.pk, sync=True)

    assert Project.history.filter(id=project.pk).latest("history_date").history_user is None


@pytest.mark.django_db
def test_a_task_still_runs_when_its_user_cant_be_looked_up(package_user):
    """django-q sends these signals without catching what receivers raise: it would stop the worker."""
    package_user["username"] = "tphimm01"

    with mock.patch("coldfront_utils.task_context.receivers.get_user_model", side_effect=RuntimeError("no db")):
        task_id = async_task("tests.test_task_context.report_who_is_acting", sync=True)

    assert Task.objects.get(id=task_id).result == {"seen": "tphimm01", "acting_user": "tphimm01"}
    assert get_acting_username() is None


@pytest.mark.django_db
def test_a_request_simple_history_already_had_is_put_back(package_user):
    """Sync mode runs the task inside the caller's process, which may be handling a request."""
    User.objects.create(username="tphimm01")
    web_request = SimpleNamespace(user=AnonymousUser())
    HistoricalRecords.context.request = web_request
    package_user["username"] = "tphimm01"
    try:
        async_task("tests.test_task_context.report_who_is_acting", sync=True)

        assert HistoricalRecords.context.request is web_request
    finally:
        del HistoricalRecords.context.request
