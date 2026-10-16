import pytest
from coldfront.core.allocation.models import AllocationAttribute, AllocationAttributeType, AllocationAttributeUsage
from coldfront.core.test_helpers.factories import (
    AAttributeTypeFactory,
    AllocationAttributeFactory,
    AllocationAttributeTypeFactory,
    AllocationFactory,
    ResourceFactory,
)
from django.core.exceptions import ValidationError

from coldfront_utils import update_allocation_attribute_usage, update_allocation_attribute_value
from coldfront_utils.attribute.attribute import create_allocation_attribute_type

pytestmark = pytest.mark.django_db

QUOTA = "Storage Quota (TB)"
PATH = "Storage path"


@pytest.fixture
def allocation():
    # Allocation.__str__, which these functions log, reads the parent resource.
    allocation = AllocationFactory()
    allocation.resources.add(ResourceFactory())
    return allocation


@pytest.fixture
def quota_type():
    return AllocationAttributeTypeFactory(
        name=QUOTA, attribute_type=AAttributeTypeFactory(name="Float"), has_usage=True
    )


@pytest.fixture
def path_type():
    return AllocationAttributeTypeFactory(name=PATH, attribute_type=AAttributeTypeFactory(name="Text"))


def attribute_on(allocation, name):
    return allocation.allocationattribute_set.get(allocation_attribute_type__name=name)


class TestUpdateAllocationAttributeValue:
    def test_a_changed_value_is_saved(self, allocation, path_type):
        AllocationAttributeFactory(allocation=allocation, allocation_attribute_type=path_type, value="/old")

        update_allocation_attribute_value(allocation, PATH, "/cluster/tufts/lab01")

        assert attribute_on(allocation, PATH).value == "/cluster/tufts/lab01"

    def test_an_unchanged_value_is_not_saved_again(self, allocation, path_type):
        """Each save writes a history row, so a sync that changes nothing must not save."""
        attribute = AllocationAttributeFactory(allocation=allocation, allocation_attribute_type=path_type, value="2048")
        history_rows = attribute.history.count()

        update_allocation_attribute_value(allocation, PATH, 2048)
        update_allocation_attribute_value(allocation, PATH, " 2048 ")

        assert attribute.history.count() == history_rows

    def test_a_missing_attribute_is_created(self, allocation, path_type):
        update_allocation_attribute_value(allocation, PATH, " /cluster/tufts/lab01 ")

        assert attribute_on(allocation, PATH).value == "/cluster/tufts/lab01"

    def test_creating_requires_the_attribute_type_to_exist(self, allocation):
        with pytest.raises(AllocationAttributeType.DoesNotExist):
            update_allocation_attribute_value(allocation, "No such type", "x")


class TestUpdateAllocationAttributeUsage:
    def test_usage_must_be_a_float(self, allocation, quota_type):
        with pytest.raises(ValidationError):
            update_allocation_attribute_usage(allocation, QUOTA, 5)

    # Saving an attribute whose type has_usage creates its usage row (at 0.0), so these set it
    # with set_usage rather than a usage factory, whose get_or_create would keep the 0.0.

    def test_a_changed_usage_is_saved(self, allocation, quota_type):
        attribute = AllocationAttributeFactory(allocation=allocation, allocation_attribute_type=quota_type, value="10")
        allocation.set_usage(QUOTA, 1.0)

        update_allocation_attribute_usage(allocation, QUOTA, 2.5)

        assert AllocationAttributeUsage.objects.get(allocation_attribute=attribute).value == 2.5

    def test_an_unchanged_usage_is_not_saved_again(self, allocation, quota_type):
        attribute = AllocationAttributeFactory(allocation=allocation, allocation_attribute_type=quota_type, value="10")
        allocation.set_usage(QUOTA, 2.5)
        usage = AllocationAttributeUsage.objects.get(allocation_attribute=attribute)
        history_rows = usage.history.count()

        update_allocation_attribute_usage(allocation, QUOTA, 2.5)

        assert usage.history.count() == history_rows

    def test_a_missing_attribute_is_created_with_its_usage(self, allocation, quota_type):
        update_allocation_attribute_usage(allocation, QUOTA, 2.5)

        attribute = attribute_on(allocation, QUOTA)
        assert attribute.allocationattributeusage.value == 2.5

    def test_usage_is_not_recorded_for_a_type_without_usage(self, allocation, path_type):
        update_allocation_attribute_usage(allocation, PATH, 2.5)

        assert not AllocationAttributeUsage.objects.filter(allocation_attribute=attribute_on(allocation, PATH)).exists()


class TestCreateAllocationAttributeType:
    def test_creates_the_type_with_the_given_flags(self):
        AAttributeTypeFactory(name="Float")

        created = create_allocation_attribute_type(QUOTA, "Float", has_usage=True, is_private=True)

        assert created.attribute_type.name == "Float"
        assert created.has_usage
        assert created.is_private
        assert not created.is_changeable

    def test_an_existing_type_is_returned_unchanged(self, quota_type):
        returned = create_allocation_attribute_type(QUOTA, "Float", has_usage=False)

        assert returned == quota_type
        assert returned.has_usage
        assert AllocationAttributeType.objects.filter(name=QUOTA).count() == 1

    def test_the_underlying_attribute_type_must_already_exist(self):
        with pytest.raises(ValidationError, match="Attribute type Nonexistent does not exist"):
            create_allocation_attribute_type(QUOTA, "Nonexistent")

        assert not AllocationAttribute.objects.exists()
        assert not AllocationAttributeType.objects.filter(name=QUOTA).exists()
