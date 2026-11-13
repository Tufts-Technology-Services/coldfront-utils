"""coldfront_utils.attribute.checks: warn when attribute types named in settings are missing or duplicated."""

from unittest.mock import patch

import pytest
from django.core import checks as django_checks
from django.db import OperationalError

from coldfront.core.allocation.models import AllocationAttributeType
from coldfront.core.resource.models import ResourceAttributeType
from coldfront.core.test_helpers import factories as f

from coldfront_utils.attribute.checks import attribute_type_warnings

pytestmark = pytest.mark.django_db

DATABASES = ["default"]


def quota_warnings(*names, databases=DATABASES, model=AllocationAttributeType):
    return attribute_type_warnings(
        model,
        names,
        databases=databases,
        check_id="storage.W002",
        consequence="requests that ask for a quota are refused",
    )


def test_types_that_exist_once_pass():
    f.AllocationAttributeTypeFactory(name="Storage Quota (TB)")
    f.AllocationAttributeTypeFactory(name="Storage Path")

    assert quota_warnings("Storage Quota (TB)", "Storage Path") == []


def test_a_missing_type_is_a_warning_naming_it_and_what_fails():
    [warning] = quota_warnings("Storage Quota (TB)")

    assert isinstance(warning, django_checks.Warning)
    assert warning.id == "storage.W002"
    assert "Storage Quota (TB)" in warning.msg
    assert "requests that ask for a quota are refused" in warning.msg


def test_a_duplicated_type_is_a_warning():
    first = f.AllocationAttributeTypeFactory(name="Storage Quota (TB)")
    AllocationAttributeType.objects.create(name="Storage Quota (TB)", attribute_type=first.attribute_type)

    [warning] = quota_warnings("Storage Quota (TB)")

    assert warning.id == "storage.W002"
    assert "2" in warning.msg and "Storage Quota (TB)" in warning.msg


def test_each_problem_is_its_own_warning():
    assert len(quota_warnings("Storage Quota (TB)", "Storage Path")) == 2


def test_other_attribute_type_models_work_too():
    f.ResourceAttributeTypeFactory(name="quota_on_request")

    assert quota_warnings("quota_on_request", model=ResourceAttributeType) == []
    [warning] = quota_warnings("ask_for_quota", model=ResourceAttributeType)
    assert "ask_for_quota" in warning.msg


@pytest.mark.parametrize("databases", [None, []])
def test_without_databases_nothing_is_queried(databases, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert quota_warnings("Storage Quota (TB)", databases=databases) == []


def test_a_table_that_cant_be_read_yet_is_no_warning():
    # before ColdFront's migrations have run: `migrate` runs database checks first
    with patch.object(AllocationAttributeType.objects, "filter", side_effect=OperationalError("no such table")):
        assert quota_warnings("Storage Quota (TB)") == []
