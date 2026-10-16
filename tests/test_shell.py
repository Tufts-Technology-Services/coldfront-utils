import subprocess
from unittest import mock

import pytest

from coldfront_utils import get_gid, get_uid


@pytest.fixture
def getent():
    """Stub out the getent subprocess; set `.return_value.stdout` to its output."""
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
    with mock.patch("coldfront_utils.shell.shell.subprocess.run", return_value=completed) as run:
        yield run


def test_get_uid_reads_the_uid_field(getent):
    getent.return_value.stdout = "jdoe01:x:51234:5000:Jane Doe:/cluster/home/jdoe01:/bin/bash\n"

    assert get_uid("jdoe01") == 51234
    assert getent.call_args.args[0] == ["getent", "passwd", "jdoe01"]


def test_get_gid_reads_the_gid_field(getent):
    getent.return_value.stdout = "lab01:x:6100:jdoe01,asmith02\n"

    assert get_gid("lab01") == 6100
    assert getent.call_args.args[0] == ["getent", "group", "lab01"]


def test_a_name_that_is_only_a_prefix_does_not_match(getent):
    getent.return_value.stdout = "jdoe01:x:51234:5000::/cluster/home/jdoe01:/bin/bash\n"

    with pytest.raises(ValueError, match="User jdoe does not exist"):
        get_uid("jdoe")


def test_unknown_user_raises(getent):
    with pytest.raises(ValueError, match="User nobody01 does not exist"):
        get_uid("nobody01")


def test_unknown_group_raises(getent):
    with pytest.raises(ValueError, match="Group nolab does not exist"):
        get_gid("nolab")
