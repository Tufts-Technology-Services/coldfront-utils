"""Helpers for system checks that the attribute types a plugin's settings name exist.

Plugins name attribute types in settings (QUOTA_ATTRIBUTE_NAME, ...) and look them up by name. A
renamed setting, a deleted type or a hand-made duplicate then fails quietly, or as a server error
when someone uses the page. Register a check with Tags.database that returns
attribute_type_warnings(...), so `migrate` and `check --database` report it:

    from django.core import checks

    @checks.register(checks.Tags.database)
    def check_quota_type(app_configs, databases=None, **kwargs):
        return attribute_type_warnings(
            AllocationAttributeType,
            [QUOTA_ATTRIBUTE_NAME],
            databases=databases,
            check_id="storage.W002",
            consequence="requests that ask for a quota are refused",
        )
"""

from django.core import checks
from django.db import DatabaseError


def attribute_type_warnings(model, names, *, databases, check_id, consequence) -> list:
    """A Warning (with id `check_id`) for each of `names` that no `model` row is named, or more than one is.

    `model` is one of ColdFront's attribute type models, such as AllocationAttributeType,
    ResourceAttributeType or ProjectAttributeType. `consequence` says what goes wrong without the type,
    for the message. Returns no warnings unless `databases` is given (Django passes it to checks
    registered with Tags.database, from `migrate` and `check --database`), or while the table can't be
    read (before ColdFront's migrations), so the check can't stop the `migrate` that creates the types.
    """
    if not databases:
        return []
    kind = model._meta.verbose_name
    warnings = []
    for name in names:
        try:
            count = model.objects.filter(name=name).count()
        except DatabaseError:
            return []  # not migrated yet
        if count == 1:
            continue
        if count == 0:
            problem = f"No {kind} is named {name!r}"
        else:
            problem = f"{count} {model._meta.verbose_name_plural} are named {name!r}"
        warnings.append(
            checks.Warning(
                f"{problem}, so {consequence}.",
                hint=f"Run migrate, make sure exactly one {kind} has that name, or check the setting that names it.",
                id=check_id,
            )
        )
    return warnings
