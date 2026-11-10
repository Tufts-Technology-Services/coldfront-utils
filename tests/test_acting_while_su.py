"""
Acting while signed in as someone else with django-su.

django-su logs an administrator in as another user, so request.user is that user. The administrator
did whatever happens next, so they are the acting user, and history records them, with a note of
whose account they were in.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django_q.signals import pre_enqueue
from django_q.tasks import async_task
from simple_history.models import HistoricalRecords

from coldfront.core.project.models import Project
from coldfront.core.test_helpers.factories import ProjectFactory

from coldfront_utils.task_context import acting_as, get_acting_username, get_impersonated_username
from coldfront_utils.task_context.middleware import ActingUserMiddleware
from coldfront_utils.task_context.su import SU_SESSION_KEY, signed_in_by

BACKEND = "django.contrib.auth.backends.ModelBackend"


def signed_in_as(user, *signed_in_before):
    """A request by `user`, in a session django-su remembers `signed_in_before` in, oldest first."""
    request = RequestFactory().get("/project/1520/")
    request.user = user
    request.session = {SU_SESSION_KEY: [[str(before.pk), BACKEND] for before in signed_in_before]}
    return request


@pytest.fixture
def admin(db):
    return User.objects.create(username="rdms_admin", is_superuser=True)


@pytest.fixture
def pi(db):
    return User.objects.create(username="pi01")


@pytest.fixture
def second_user(db):
    return User.objects.create(username="clee45")


class TestSignedInBy:
    def test_is_the_user_django_su_remembers(self, admin, pi):
        assert signed_in_by(signed_in_as(pi, admin)) == admin

    def test_is_the_first_user_when_they_signed_in_as_one_user_and_then_another(self, admin, pi, second_user):
        assert signed_in_by(signed_in_as(second_user, admin, pi)) == admin

    def test_is_nobody_when_django_su_remembers_no_one(self, pi):
        assert signed_in_by(signed_in_as(pi)) is None

    def test_is_nobody_for_a_request_with_no_session(self, pi):
        request = RequestFactory().get("/")
        request.user = pi

        assert signed_in_by(request) is None

    def test_is_nobody_when_that_user_has_since_been_deleted(self, admin, pi):
        request = signed_in_as(pi, admin)
        admin.delete()

        assert signed_in_by(request) is None


class TestActingUserMiddleware:
    @staticmethod
    def run(request):
        """Handle the request, and say who was acting, and as whom, while it was."""
        seen = {}

        def view(request):
            seen["acting"] = get_acting_username()
            seen["impersonated"] = get_impersonated_username()

        ActingUserMiddleware(view)(request)
        return seen

    def test_the_administrator_is_acting_while_signed_in_as_someone(self, admin, pi):
        assert self.run(signed_in_as(pi, admin))["acting"] == "rdms_admin"

    def test_the_account_they_are_in_is_named(self, admin, pi):
        assert self.run(signed_in_as(pi, admin))["impersonated"] == "pi01"

    def test_the_first_administrator_is_acting_however_many_times_they_signed_in_as_someone(
        self, admin, pi, second_user
    ):
        seen = self.run(signed_in_as(second_user, admin, pi))

        assert seen == {"acting": "rdms_admin", "impersonated": "clee45"}

    def test_a_user_who_signed_in_themselves_is_acting_as_themselves(self, pi):
        assert self.run(signed_in_as(pi)) == {"acting": "pi01", "impersonated": None}

    def test_a_request_with_no_session_is_the_users_own(self, pi):
        request = RequestFactory().get("/")
        request.user = pi

        assert self.run(request) == {"acting": "pi01", "impersonated": None}

    def test_the_users_own_account_is_used_when_the_administrator_no_longer_exists(self, admin, pi):
        request = signed_in_as(pi, admin)
        admin.delete()

        assert self.run(request) == {"acting": "pi01", "impersonated": None}

    def test_nobody_is_acting_for_an_anonymous_request(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        assert self.run(request) == {"acting": None, "impersonated": None}

    def test_the_request_leaves_nobody_acting_or_impersonated_when_it_ends(self, admin, pi):
        self.run(signed_in_as(pi, admin))

        assert get_acting_username() is None
        assert get_impersonated_username() is None

    def test_nor_when_the_view_raises(self, admin, pi):
        def failing_view(request):
            raise RuntimeError("the view broke")

        with pytest.raises(RuntimeError):
            ActingUserMiddleware(failing_view)(signed_in_as(pi, admin))

        assert get_acting_username() is None
        assert get_impersonated_username() is None


class TestSignedInByOnTheRequest:
    """Templates read request.signed_in_by, for a banner that names the administrator."""

    @staticmethod
    def seen_on_request(request):
        seen = []
        ActingUserMiddleware(lambda request: seen.append(getattr(request, "signed_in_by", "not set")))(request)
        return seen[0]

    def test_is_the_administrator_while_they_are_signed_in_as_someone(self, admin, pi):
        assert self.seen_on_request(signed_in_as(pi, admin)) == admin

    def test_is_nobody_for_a_user_who_signed_in_themselves(self, pi):
        assert self.seen_on_request(signed_in_as(pi)) is None

    def test_is_nobody_when_the_administrator_no_longer_exists(self, admin, pi):
        request = signed_in_as(pi, admin)
        admin.delete()

        assert self.seen_on_request(request) is None

    def test_is_nobody_for_an_anonymous_request(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        assert self.seen_on_request(request) is None


def test_acting_as_can_name_the_account_it_is_in():
    with acting_as("rdms_admin", impersonating="pi01"):
        assert get_acting_username() == "rdms_admin"
        assert get_impersonated_username() == "pi01"
    assert get_impersonated_username() is None


def test_acting_as_puts_back_who_was_impersonated_before():
    with acting_as("rdms_admin", impersonating="pi01"):
        with acting_as("cbarne02"):
            assert get_impersonated_username() is None
        assert get_impersonated_username() == "pi01"


@pytest.fixture
def history_request():
    """Stand in for HistoryRequestMiddleware: simple_history reads the request's user from here."""

    def keep_request_for(user):
        HistoricalRecords.context.request = SimpleNamespace(user=user)

    yield keep_request_for
    if hasattr(HistoricalRecords.context, "request"):
        del HistoricalRecords.context.request


@pytest.mark.django_db
class TestHistoryWhileSignedInAsSomeone:
    def test_a_row_names_the_administrator_not_the_account(self, admin, pi, history_request):
        history_request(pi)

        with acting_as("rdms_admin", impersonating="pi01"):
            project = ProjectFactory()

        assert project.history.latest("history_date").history_user == admin

    def test_and_says_whose_account_they_were_in(self, admin, pi, history_request):
        history_request(pi)

        with acting_as("rdms_admin", impersonating="pi01"):
            project = ProjectFactory()

        assert project.history.latest("history_date").history_change_reason == "signed in as pi01"

    def test_an_existing_reason_is_kept(self, admin, pi, history_request):
        history_request(pi)
        project = ProjectFactory()
        project.title = "Retitled"
        project._change_reason = "fixing a typo"

        with acting_as("rdms_admin", impersonating="pi01"):
            project.save()

        reason = project.history.latest("history_date").history_change_reason
        assert reason == "fixing a typo (signed in as pi01)"

    def test_a_long_reason_is_cut_to_fit_the_column(self, admin, pi, history_request):
        history_request(pi)
        project = ProjectFactory()
        project.title = "Retitled"
        project._change_reason = "x" * 100

        with acting_as("rdms_admin", impersonating="pi01"):
            project.save()

        reason = project.history.latest("history_date").history_change_reason
        assert len(reason) == 100
        assert reason.endswith("signed in as pi01)")

    def test_a_user_set_on_the_instance_is_left_alone(self, admin, pi, second_user, history_request):
        history_request(pi)
        project = ProjectFactory()
        project.title = "Retitled"
        project._history_user = second_user

        with acting_as("rdms_admin", impersonating="pi01"):
            project.save()

        assert project.history.latest("history_date").history_user == second_user

    def test_a_row_written_by_a_user_signed_in_as_themselves_is_unchanged(self, pi, history_request):
        history_request(pi)

        with acting_as("pi01"):
            project = ProjectFactory()

        latest = project.history.latest("history_date")
        assert latest.history_user == pi
        assert latest.history_change_reason in (None, "")

    def test_a_row_written_when_an_administrator_cannot_be_found_keeps_the_requests_user(self, pi, history_request):
        history_request(pi)

        with acting_as("someone_deleted", impersonating="pi01"):
            project = ProjectFactory()

        assert project.history.latest("history_date").history_user == pi


@pytest.fixture
def packages():
    """The packages of the tasks queued during a test, as the pre_enqueue receivers left them."""
    seen = []

    def record_package(sender, task, **kwargs):
        seen.append(dict(task))

    pre_enqueue.connect(record_package, dispatch_uid="test-record-su-package")
    yield seen
    pre_enqueue.disconnect(dispatch_uid="test-record-su-package")


@pytest.mark.django_db
def test_a_task_queued_while_signed_in_as_someone_carries_the_administrator(admin, pi, packages):
    def view(request):
        async_task("tests.test_task_context.return_lab_name", sync=True)

    ActingUserMiddleware(view)(signed_in_as(pi, admin))

    assert [package["acting_user"] for package in packages] == ["rdms_admin"]


def test_projects_are_the_models_these_tests_write_history_for():
    """If Project stopped keeping history, the history tests above would pass for the wrong reason."""
    assert hasattr(Project, "history")
