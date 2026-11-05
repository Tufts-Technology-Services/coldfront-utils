"""Utilities for ColdFront and its plugins.

The helpers listed here are imported from their modules on first use, not when the package is.
Some of those modules import ColdFront's models, which Django only allows once its apps have
loaded, and coldfront_utils.task_context is itself an app that Django imports while they're
loading: importing them eagerly here would make that impossible. `from coldfront_utils import
ttl_cache` and the rest work as before.
"""

import importlib

# each helper, and the module it comes from
_HELPERS = {
    "update_allocation_attribute_usage": ".attribute",
    "update_allocation_attribute_value": ".attribute",
    "ttl_cache": ".cache",
    "json_default": ".mail",
    "mail_json_report": ".mail",
    "mail_report": ".mail",
    "get_uid": ".shell",
    "get_gid": ".shell",
    "bytes_to_units": ".util",
    "units_to_bytes": ".util",
    "validate_posix_path": ".validation",
}

__all__ = list(_HELPERS)


def __getattr__(name):
    if name not in _HELPERS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    helper = getattr(importlib.import_module(_HELPERS[name], __name__), name)
    # kept, so the next lookup doesn't come back here
    globals()[name] = helper
    return helper


def __dir__():
    return sorted({*globals(), *_HELPERS})
