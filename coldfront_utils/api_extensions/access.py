"""ColdFront's own rules for who may see a project or an allocation, for extensions that follow them."""


def can_view_project(caller, project) -> bool:
    """Whether ColdFront's project page would show caller this project: superusers, holders of
    project.can_view_all_projects, and the project's Active members."""
    if caller.is_superuser or caller.has_perm("project.can_view_all_projects"):
        return True
    return project.projectuser_set.filter(user=caller, status__name="Active").exists()


def can_view_allocation(caller, allocation) -> bool:
    """Whether ColdFront's allocation page would show caller this allocation: holders of
    allocation.can_view_all_allocations, and anyone Allocation.has_perm gives USER (superusers, the
    project's managers and PI, and the allocation's users)."""
    # imported here, so a plugin can import this module before the apps are loaded
    from coldfront.core.allocation.models import AllocationPermission

    if caller.has_perm("allocation.can_view_all_allocations"):
        return True
    return allocation.has_perm(caller, AllocationPermission.USER)
