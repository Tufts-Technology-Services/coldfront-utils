"""Utilities for ColdFront and its plugins.

The helpers listed here are imported from their modules on first use, not when the package is.
Some of those modules import ColdFront's models, which Django only allows once its apps have
loaded, and coldfront_utils.task_context is itself an app that Django imports while they're
loading: importing them eagerly here would make that impossible. `from coldfront_utils import
ttl_cache` and the rest work as before.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Never run: TYPE_CHECKING is False at runtime. Type checkers and IDEs read these imports, so
    # they see where each helper comes from and what it is, which __getattr__ hides from them.
    # Keep them in step with _HELPERS below (tests/test_package.py checks that they are).
    # "x as x" marks each as re-exported on purpose, for ruff and type checkers alike
    from .attribute import update_allocation_attribute_usage as update_allocation_attribute_usage
    from .attribute import update_allocation_attribute_value as update_allocation_attribute_value
    from .cache import ttl_cache as ttl_cache
    from .mail import json_default as json_default
    from .mail import mail_json_report as mail_json_report
    from .mail import mail_report as mail_report
    from .shell import get_gid as get_gid
    from .shell import get_uid as get_uid
    from .util import bytes_to_units as bytes_to_units
    from .util import units_to_bytes as units_to_bytes
    from .validation import validate_posix_path as validate_posix_path

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
