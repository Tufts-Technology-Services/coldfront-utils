"""can_view_project and can_view_allocation follow ColdFront's project and allocation pages."""

import pytest
from django.contrib.auth.models import Permission

from coldfront.core.test_helpers import factories as f

from coldfront_utils.api_extensions import can_view_allocation, can_view_project

pytestmark = pytest.mark.django_db


def grant_permission(user, app_label, codename):
    """Give user a model permission, and return the user fetched again, so has_perm sees it."""
    user.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    return type(user).objects.get(pk=user.pk)


def make_member(project, username, role="User", status="Active"):
    """A ProjectUser row for a new user on project; returns the user."""
    user = f.UserFactory(username=username)
    f.ProjectUserFactory(
        project=project,
        user=user,
        role=f.ProjectUserRoleChoiceFactory(name=role),
        status=f.ProjectUserStatusChoiceFactory(name=status),
    )
    return user


@pytest.fixture
def project():
    return f.ProjectFactory(status=f.ProjectStatusChoiceFactory(name="Active"))


@pytest.fixture
def allocation(project):
    return f.AllocationFactory(project=project)


def test_a_superuser_can_view_any_project(project):
    assert can_view_project(f.UserFactory(username="root01", is_superuser=True), project)


def test_a_holder_of_can_view_all_projects_can_view_any_project(project):
    viewer = grant_permission(f.UserFactory(username="viewer01"), "project", "can_view_all_projects")

    assert can_view_project(viewer, project)


def test_an_active_member_can_view_their_project(project):
    assert can_view_project(make_member(project, "member01"), project)


@pytest.mark.parametrize("status", ["Removed", "Pending"])
def test_a_member_who_is_not_active_cannot_view_the_project(project, status):
    assert not can_view_project(make_member(project, "member01", status=status), project)


def test_someone_with_no_part_in_a_project_cannot_view_it(project):
    assert not can_view_project(f.UserFactory(username="stranger01"), project)


def test_staff_alone_cannot_view_a_project(project):
    assert not can_view_project(f.UserFactory(username="staff01", is_staff=True), project)


def test_can_view_all_allocations_does_not_open_projects(project):
    viewer = grant_permission(f.UserFactory(username="viewer01"), "allocation", "can_view_all_allocations")

    assert not can_view_project(viewer, project)


def test_a_superuser_can_view_any_allocation(allocation):
    assert can_view_allocation(f.UserFactory(username="root01", is_superuser=True), allocation)


def test_a_holder_of_can_view_all_allocations_can_view_any_allocation(allocation):
    viewer = grant_permission(f.UserFactory(username="viewer01"), "allocation", "can_view_all_allocations")

    assert can_view_allocation(viewer, allocation)


def test_a_project_manager_can_view_the_projects_allocations(project, allocation):
    assert can_view_allocation(make_member(project, "manager01", role="Manager"), allocation)


@pytest.mark.parametrize("status", ["Active", "New", "PendingEULA"])
def test_a_project_member_on_the_allocation_can_view_it(project, allocation, status):
    member = make_member(project, "member01")
    f.AllocationUserFactory(allocation=allocation, user=member, status=f.AllocationUserStatusChoiceFactory(name=status))

    assert can_view_allocation(member, allocation)


def test_a_project_member_not_on_the_allocation_cannot_view_it(project, allocation):
    assert not can_view_allocation(make_member(project, "member01"), allocation)


def test_an_allocation_user_no_longer_on_the_project_cannot_view_it(project, allocation):
    former = make_member(project, "former01", status="Removed")
    f.AllocationUserFactory(allocation=allocation, user=former)

    assert not can_view_allocation(former, allocation)


def test_someone_with_no_part_in_an_allocation_cannot_view_it(allocation):
    assert not can_view_allocation(f.UserFactory(username="stranger01"), allocation)


def test_can_view_all_projects_does_not_open_allocations(allocation):
    viewer = grant_permission(f.UserFactory(username="viewer01"), "project", "can_view_all_projects")

    assert not can_view_allocation(viewer, allocation)
