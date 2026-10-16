import warnings
from unittest import mock

import pytest
from django.core.cache import cache
from django.core.cache.backends.base import CacheKeyWarning

from coldfront_utils import ttl_cache


@pytest.fixture(autouse=True)
def empty_cache():
    cache.clear()
    yield
    cache.clear()


def counted(return_value, module=__name__):
    """A ttl_cache-wrapped function defined in `module`, and the mock that records its real calls."""
    inner = mock.Mock(return_value=return_value)

    def lookup_group(*args, **kwargs):
        return inner(*args, **kwargs)

    lookup_group.__module__ = module
    return ttl_cache()(lookup_group), inner


def test_repeat_calls_are_served_from_the_cache():
    lookup_group, inner = counted("lab01")

    assert lookup_group("jdoe01") == "lab01"
    assert lookup_group("jdoe01") == "lab01"
    inner.assert_called_once_with("jdoe01")


def test_different_arguments_are_cached_separately():
    lookup_group, inner = counted("lab01")

    lookup_group("jdoe01")
    lookup_group("asmith02")
    lookup_group("jdoe01", refresh=True)

    assert inner.call_count == 3


def test_keyword_argument_order_does_not_matter():
    lookup_group, inner = counted("lab01")

    lookup_group(username="jdoe01", refresh=True)
    lookup_group(refresh=True, username="jdoe01")

    inner.assert_called_once()


def test_same_named_functions_in_different_modules_are_cached_separately():
    grouper_lookup, grouper_inner = counted("grouper-lab", module="account_signup.grouper_utils")
    vast_lookup, vast_inner = counted("vast-lab", module="storage.vast.vast")

    assert grouper_lookup("jdoe01") == "grouper-lab"
    assert vast_lookup("jdoe01") == "vast-lab"
    grouper_inner.assert_called_once()
    vast_inner.assert_called_once()


def test_keys_are_valid_for_memcached():
    """Django warns, rather than fails, on keys memcached would reject; make that a failure here."""
    lookup_group, _ = counted("lab01")

    with warnings.catch_warnings():
        warnings.simplefilter("error", CacheKeyWarning)
        lookup_group("name with spaces\nand a newline", note="x" * 500)


def test_none_is_not_cached():
    """A None result is indistinguishable from a cache miss, so it is recomputed every time."""
    lookup_group, inner = counted(None)

    lookup_group("jdoe01")
    lookup_group("jdoe01")

    assert inner.call_count == 2


def test_falsy_results_other_than_none_are_cached():
    lookup_group, inner = counted(False)

    assert lookup_group("jdoe01") is False
    assert lookup_group("jdoe01") is False
    inner.assert_called_once()


def test_timeout_is_passed_to_the_cache():
    with mock.patch.object(cache, "set", wraps=cache.set) as cache_set:

        @ttl_cache(timeout=30)
        def lookup_group(username):
            return "lab01"

        lookup_group("jdoe01")

    assert cache_set.call_args.kwargs["timeout"] == 30


def test_wrapped_function_keeps_its_name():
    lookup_group, _ = counted("lab01")

    assert lookup_group.__name__ == "lookup_group"
