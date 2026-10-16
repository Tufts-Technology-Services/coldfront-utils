import time

import pytest
from django.core.exceptions import ValidationError

from coldfront_utils import validate_posix_path


@pytest.mark.parametrize(
    "path",
    ["/cluster/tufts/lab01", "relative/dir", "/cluster/tufts/lab01/", "/name with spaces/ok"],
)
def test_valid_paths_pass(path):
    validate_posix_path(path)


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("", id="empty"),
        pytest.param("/", id="root"),
        pytest.param("/cluster//lab01", id="empty-component"),
        pytest.param("/cluster/lab\0name", id="null-byte"),
    ],
)
def test_invalid_paths_are_rejected(path):
    with pytest.raises(ValidationError):
        validate_posix_path(path)


@pytest.mark.parametrize("near_miss", ["/" + "a" * 30 + "//", "a" * 30 + "\0"])
def test_a_near_miss_is_rejected_quickly(near_miss):
    """The previous pattern backtracked exponentially here: about 45 seconds at this length."""
    started = time.perf_counter()
    with pytest.raises(ValidationError):
        validate_posix_path(near_miss)
    assert time.perf_counter() - started < 1
