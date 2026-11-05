"""coldfront_utils imports its helpers on first use, and tells static tools where they come from.

The helpers are listed twice in coldfront_utils/__init__.py: in _HELPERS, which __getattr__ imports
them from at runtime, and in an `if TYPE_CHECKING:` block that only type checkers and IDEs read.
These tests keep the two in step, and check each entry really names its helper's module.
"""

import ast
import importlib
from pathlib import Path

import pytest

import coldfront_utils

INIT = Path(coldfront_utils.__file__)


def type_checking_imports():
    """{name: module} for the imports inside `if TYPE_CHECKING:` in coldfront_utils/__init__.py."""
    imports = {}
    for node in ast.parse(INIT.read_text()).body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for statement in node.body:
                if isinstance(statement, ast.ImportFrom):
                    for alias in statement.names:
                        imports[alias.asname or alias.name] = "." * statement.level + (statement.module or "")
    return imports


def test_static_tools_see_every_helper_from_the_module_it_comes_from():
    assert type_checking_imports() == coldfront_utils._HELPERS


@pytest.mark.parametrize("name, module", sorted(coldfront_utils._HELPERS.items()))
def test_each_helper_comes_from_the_module_it_is_listed_under(name, module):
    expected = getattr(importlib.import_module(module, "coldfront_utils"), name)

    assert getattr(coldfront_utils, name) is expected


def test_every_helper_is_exported():
    assert sorted(coldfront_utils.__all__) == sorted(coldfront_utils._HELPERS)


def test_an_unknown_name_is_still_an_attribute_error():
    with pytest.raises(AttributeError, match="no attribute 'not_a_helper'"):
        coldfront_utils.not_a_helper
