import logging
from django.core.exceptions import ValidationError
from coldfront.core.allocation.models import AllocationAttribute, AllocationAttributeType

logger = logging.getLogger(__name__)



def update_allocation_attribute_usage(allocation, attribute_name, usage_value: float):
    """Task to update the usage value of an allocation attribute for a specific allocation"""

    if not isinstance(usage_value, float):
        raise ValidationError('Provided usage value is not a float')
    attribute = allocation.allocationattribute_set.filter(allocation_attribute_type__name=attribute_name).first()
    if attribute:
        if attribute.allocation_attribute_type.has_usage:
            allocation.set_usage(attribute_name, usage_value)
            logger.info(f"Updated usage for {attribute_name} of allocation {allocation} to {usage_value}")
        else:
            logger.warning(f"{attribute_name} attribute for allocation {allocation} does not have usage enabled. Cannot update usage.")
    else:
        logger.warning(f"No {attribute_name} attribute found for allocation {allocation}. Creating new attribute.")
        attr, _ = AllocationAttribute.objects.get_or_create(
            allocation=allocation,
            allocation_attribute_type=AllocationAttributeType.objects.get(name=attribute_name),
            value=usage_value # setting to usage_value for now, but this should be set to the actual value for the attribute when that information is available
        )
        if attr.allocation_attribute_type.has_usage:
            allocation.set_usage(attribute_name, usage_value)
            logger.info(f"Created {attribute_name} attribute and set usage for allocation {allocation} to {usage_value}")
        else:
            logger.warning(f"{attribute_name} attribute for allocation {allocation} does not have usage enabled. Cannot set usage.")


def update_allocation_attribute_value(allocation, attribute_name, attribute_value):
    """Task to update the value of an allocation attribute for a specific allocation"""
    attribute = allocation.allocationattribute_set.filter(allocation_attribute_type__name=attribute_name).first()
    attribute_value_str = str(attribute_value).strip()
    if attribute:
        attribute.value = attribute_value_str
        attribute.save()
        logger.info(f"Updated {attribute_name} for allocation {allocation} to {attribute_value_str}")
    else:
        logger.warning(f"No {attribute_name} attribute found for allocation {allocation}. Creating new attribute.")
        AllocationAttribute.objects.create(
            allocation=allocation,
            allocation_attribute_type=AllocationAttributeType.objects.get(name=attribute_name),
            value=attribute_value_str
        )
        logger.info(f"Created {attribute_name} attribute and set value for allocation {allocation} to {attribute_value_str}")
