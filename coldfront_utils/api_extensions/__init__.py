"""Places a plugin can attach its own content to the REST API, without either importing the other.

A plugin subclasses PersonExtension, ProjectExtension or AllocationExtension and registers the
class in its AppConfig.ready() with register_api_extension(). The API (coldfront-rest-api-plugin)
serves each registered extension at users/<username>/<name>/, projects/<id>/<name>/ or
allocations/<id>/<name>/, and <name>/<key>/ for its items, knowing only this module. Without the
API installed, registering does nothing; without the plugin, its routes are 404s.

The extension decides who may see its content, in allows(). can_view_project() and
can_view_allocation() apply ColdFront's own page rules, for extensions that follow them.

This isn't a Django app: there's nothing to add to INSTALLED_APPS. See "Plugin extensions" in
coldfront-rest-api-plugin's docs/rest-api-plan.md for the design.
"""

from .access import can_view_allocation, can_view_project
from .extensions import (
    AllocationExtension,
    ApiExtension,
    ExtensionBadRequest,
    ExtensionNotFound,
    ExtensionUnavailable,
    PersonExtension,
    ProjectExtension,
    find_extension,
    register_api_extension,
    registered_extensions,
)

__all__ = [
    "AllocationExtension",
    "ApiExtension",
    "ExtensionBadRequest",
    "ExtensionNotFound",
    "ExtensionUnavailable",
    "PersonExtension",
    "ProjectExtension",
    "can_view_allocation",
    "can_view_project",
    "find_extension",
    "register_api_extension",
    "registered_extensions",
]
