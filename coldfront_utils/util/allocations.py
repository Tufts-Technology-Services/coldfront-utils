from coldfront.core.allocation.models import AllocationAttribute


def get_alloc_volpath(volpath, attribute_name='sf_vol_path'):
    attr = AllocationAttribute.objects.filter(allocation_attribute_type__name=attribute_name, value=volpath)
    if attr.exists():
        attr = attr.first()
    return attr.allocation if attr else None


def change_resource(volpath, resource):
     alloc = get_alloc_volpath(volpath)
     if alloc:
         alloc.resources.set([resource])
         alloc.save()
     else:
         print(f'no allocation for {volpath}')