"""reschedulable: a task that can't finish yet schedules itself to run again, with the same arguments.

The tasks are defined at module level, as real ones are, because a retry finds its task by dotted
path. Each body hands its arguments to `body`, a mock the tests set up to finish, fail or wait.
"""

import ast
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.utils import timezone
from django.utils.module_loading import import_string
from django_q.conf import Conf
from django_q.models import Schedule, Task
from django_q.scheduler import scheduler

from coldfront_utils.tasks import SCHEDULED, Rescheduled, RetryLater, build_task_result, reschedulable

body = mock.Mock()


class GroupNotInADYet(Exception):
    pass


@reschedulable(retry_on=(GroupNotInADYet,), retries=3, wait=5)
def provision_lab_share(path, quota_bytes, owner="jdoe01", share_provisioned=False):
    return body(path=path, quota_bytes=quota_bytes, owner=owner, share_provisioned=share_provisioned)


@reschedulable(q_options={"group": "starfish"})
def index_lab_volume(allocation_id, scan_id=None):
    return body(allocation_id=allocation_id, scan_id=scan_id)


@reschedulable(q_options=lambda allocation_id, **_: {"task_name": f"index_allocation_{allocation_id}"})
def index_named_volume(allocation_id, scan_id=None):
    return body(allocation_id=allocation_id, scan_id=scan_id)


@reschedulable(retry_on=(GroupNotInADYet,), retries=2)
def provision_group_share(path, group):
    return body(path=path, group=group)


@reschedulable()
def tag_lab_volume(volume, **tags):
    return body(volume=volume, **tags)


@pytest.fixture(autouse=True)
def reset_body():
    body.reset_mock(return_value=True, side_effect=True)
    body.return_value = "done"


@pytest.fixture
def scheduled():
    """django-q's schedule(), so a test can see what retry was asked for without a database."""
    with mock.patch("coldfront_utils.tasks.reschedulable.schedule") as stand_in:
        yield stand_in


def retry_arguments(scheduled):
    """The task path and keyword arguments a retry was scheduled with, minus django-q's own."""
    path = scheduled.call_args.args[0]
    arguments = {k: v for k, v in scheduled.call_args.kwargs.items() if k not in ("schedule_type", "next_run")}
    return path, arguments


# --- a run that finishes -----------------------------------------------------------------------


def test_a_run_that_finishes_returns_its_own_result(scheduled):
    assert provision_lab_share("/projects/lab", 10) == "done"

    scheduled.assert_not_called()


def test_an_error_it_isnt_told_to_retry_on_propagates(scheduled):
    body.side_effect = ValueError("bad quota")

    with pytest.raises(ValueError, match="bad quota"):
        provision_lab_share("/projects/lab", 10)

    scheduled.assert_not_called()


# --- a run that has to wait --------------------------------------------------------------------


def test_a_retried_error_schedules_the_same_call_again(scheduled):
    body.side_effect = GroupNotInADYet("group lab not in AD yet")
    before = timezone.now()

    result = provision_lab_share("/projects/lab", 10, owner="asmith02")

    path, arguments = retry_arguments(scheduled)
    assert import_string(path) is provision_lab_share
    # positional arguments are passed by keyword, and defaults the call didn't give are left out
    assert arguments == {
        "path": "/projects/lab",
        "quota_bytes": 10,
        "owner": "asmith02",
        "retries": 2,
        "wait": 5,
        "q_options": {},
    }
    assert scheduled.call_args.kwargs["schedule_type"] == Schedule.ONCE
    next_run = scheduled.call_args.kwargs["next_run"]
    assert before + timedelta(minutes=5) <= next_run <= timezone.now() + timedelta(minutes=5)
    assert result == {
        "message": "scheduled:group lab not in AD yet (runs again in 5 minutes, 2 retries left)",
        "reason": "group lab not in AD yet",
        "retries_left": 2,
        "next_run": next_run.isoformat(),
    }


def test_a_rescheduled_run_can_be_told_apart_from_a_finished_one(scheduled):
    """So a state-recording decorator around the task can record that it's waiting, not done."""
    body.side_effect = GroupNotInADYet("group lab not in AD yet")

    result = provision_lab_share("/projects/lab", 10)

    assert isinstance(result, Rescheduled)
    assert isinstance(result, dict)
    assert result["message"].startswith(f"{SCHEDULED}:")


def test_retry_later_carries_progress_into_the_next_run(scheduled):
    """A task that got partway can skip the part that's done when it runs again."""
    body.side_effect = RetryLater("directory not visible yet", share_provisioned=True)

    result = provision_lab_share("/projects/lab", 10)

    _, arguments = retry_arguments(scheduled)
    assert arguments["share_provisioned"] is True
    assert result["reason"] == "directory not visible yet"


def test_retry_later_works_without_retry_on(scheduled):
    """A polling task waits on a status, not an exception, so it only ever raises RetryLater."""
    body.side_effect = RetryLater("not indexed in Starfish yet", scan_id=42)

    index_lab_volume(2326)

    path, arguments = retry_arguments(scheduled)
    assert import_string(path) is index_lab_volume
    assert arguments == {
        "allocation_id": 2326,
        "scan_id": 42,
        "retries": 4,
        "wait": 5,
        "q_options": {"group": "starfish"},
    }


def test_q_options_can_be_built_from_the_arguments(scheduled):
    body.side_effect = RetryLater("not indexed yet")

    index_named_volume(2326)

    assert scheduled.call_args.kwargs["q_options"] == {"task_name": "index_allocation_2326"}


def test_keyword_arguments_a_task_collects_are_passed_on_as_they_came(scheduled):
    body.side_effect = RetryLater("volume busy")

    tag_lab_volume("vol1", owner="jdoe01", approver="asmith02")

    _, arguments = retry_arguments(scheduled)
    assert arguments["volume"] == "vol1"
    assert arguments["owner"] == "jdoe01"
    assert arguments["approver"] == "asmith02"
    assert "tags" not in arguments


def test_a_call_can_set_its_own_retries_and_wait(scheduled):
    body.side_effect = GroupNotInADYet("not yet")

    result = provision_lab_share("/projects/lab", 10, retries=9, wait=1)

    _, arguments = retry_arguments(scheduled)
    assert arguments["retries"] == 8
    assert arguments["wait"] == 1
    assert "runs again in 1 minutes, 8 retries left" in result["message"]


# --- running out of retries --------------------------------------------------------------------


def test_with_no_retries_left_the_error_is_raised(scheduled):
    error = GroupNotInADYet("group lab not in AD yet")
    body.side_effect = error

    with pytest.raises(GroupNotInADYet) as raised:
        provision_lab_share("/projects/lab", 10, retries=0)

    assert raised.value is error
    scheduled.assert_not_called()


def test_with_no_retries_left_retry_later_is_raised_with_its_reason(scheduled):
    body.side_effect = RetryLater("not indexed in Starfish yet", scan_id=42)

    with pytest.raises(RetryLater, match="not indexed in Starfish yet"):
        index_lab_volume(2326, retries=0)

    scheduled.assert_not_called()


def directory_not_visible(**_):
    """A body's wait, the way a task raises it: from the error that caused it."""
    try:
        raise FileNotFoundError("/cluster/tufts/lab isn't visible yet")
    except FileNotFoundError as error:
        raise RetryLater(str(error), share_provisioned=True) from error


def test_with_no_retries_left_retry_later_gives_way_to_the_error_it_was_raised_from(scheduled):
    """So the task fails with the real error, not the wrapper that asked for the wait."""
    body.side_effect = directory_not_visible

    with pytest.raises(FileNotFoundError, match="isn't visible yet"):
        provision_lab_share("/projects/lab", 10, retries=0)

    scheduled.assert_not_called()


# --- arguments a retry couldn't use ------------------------------------------------------------


def test_an_argument_django_q_couldnt_read_back_is_refused_before_scheduling(scheduled):
    """django-q would read the arguments back as {}, and run the retry with none of them."""
    body.side_effect = GroupNotInADYet("not yet")

    with pytest.raises(TypeError, match="path=PosixPath"):
        provision_lab_share(Path("/projects/lab"), 10)

    scheduled.assert_not_called()


def test_a_change_to_an_argument_the_task_doesnt_take_is_refused(scheduled):
    body.side_effect = RetryLater("not yet", share_provisoned=True)

    with pytest.raises(TypeError, match="share_provisoned"):
        provision_lab_share("/projects/lab", 10)

    scheduled.assert_not_called()


def test_retry_later_cant_change_the_retry_budget(scheduled):
    body.side_effect = RetryLater("not yet", retries=100)

    with pytest.raises(TypeError, match="can't change retries"):
        provision_lab_share("/projects/lab", 10)

    scheduled.assert_not_called()


# --- tasks the decorator refuses ---------------------------------------------------------------


def test_a_task_defined_inside_a_function_is_refused():
    with pytest.raises(TypeError, match="module level"):

        @reschedulable()
        def nested_task(allocation_id):
            pass


def positional_only_task(allocation_id, /):
    pass


def collects_volumes_task(*volumes):
    pass


def own_retries_task(allocation_id, retries=5):
    pass


def timeout_task(allocation_id, timeout=30):
    pass


def named_task(allocation_id, name=None):
    pass


def acting_user_task(allocation_id, _acting_user=None):
    pass


@pytest.mark.parametrize(
    "task, problem",
    [
        pytest.param(positional_only_task, "positional-only", id="positional-only"),
        pytest.param(collects_volumes_task, r"\*volumes", id="var-positional"),
        pytest.param(own_retries_task, "retries", id="retry-budget"),
        pytest.param(timeout_task, "timeout", id="async-task-option"),
        pytest.param(named_task, "name", id="schedule-option"),
        pytest.param(acting_user_task, "_acting_user", id="acting-user"),
    ],
)
def test_a_task_its_retry_couldnt_call_the_same_way_is_refused(task, problem):
    with pytest.raises(TypeError, match=problem):
        reschedulable()(task)


def test_retry_on_must_be_exception_types():
    with pytest.raises(TypeError, match="exception types"):
        reschedulable(retry_on=("GroupNotInADYet",))


# --- through django-q ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_scheduled_retry_runs_the_task_again_with_its_arguments():
    """Store the schedule for real, read it back as django-q's scheduler does, and run it."""
    body.side_effect = [RetryLater("directory not visible yet", share_provisioned=True), "done"]

    provision_lab_share("/projects/lab", 10, owner="asmith02")

    stored = Schedule.objects.get()
    assert stored.schedule_type == Schedule.ONCE
    arguments = ast.literal_eval(stored.kwargs)
    q_options = arguments.pop("q_options")
    assert q_options == {}
    assert import_string(stored.func)(**arguments) == "done"
    assert body.call_args.kwargs == {
        "path": "/projects/lab",
        "quota_bytes": 10,
        "owner": "asmith02",
        "share_provisioned": True,
    }


@pytest.mark.django_db
def test_a_group_argument_reaches_the_retry_through_django_qs_own_scheduler():
    """django-q takes "group" as a queue option, but only when its q_options has none, and the
    scheduler always gives it one. So a task can have a group parameter: share tasks do.

    Runs the retry the way a cluster would: django-q's scheduler() picks up the due schedule and
    hands it to async_task(), which sync mode runs in this process.
    """
    body.side_effect = [GroupNotInADYet("group lab01 not in AD yet"), "done"]
    provision_group_share("/projects/lab", "lab01")
    Schedule.objects.update(next_run=timezone.now() - timedelta(minutes=1))

    with mock.patch.object(Conf, "SYNC", True):
        scheduler()

    assert body.call_count == 2
    assert body.call_args.kwargs == {"path": "/projects/lab", "group": "lab01"}
    assert Task.objects.get().result == "done"


# --- the result format ---------------------------------------------------------------------------


def test_a_result_is_its_state_and_reason():
    assert build_task_result("successful") == {"message": "successful:"}
    assert build_task_result("failed", "no quota") == {"message": "failed:no quota"}


def test_a_returned_dict_is_carried_through_but_message_is_reserved():
    returned = {"path": "/projects/lab", "message": "something else"}

    assert build_task_result("successful", "", returned) == {"path": "/projects/lab", "message": "successful:"}
    assert returned["message"] == "something else"


def test_any_other_returned_value_is_kept_under_result():
    assert build_task_result("successful", "", ["a", "b"]) == {"result": ["a", "b"], "message": "successful:"}
