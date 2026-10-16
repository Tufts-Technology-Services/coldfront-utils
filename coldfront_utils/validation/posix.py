import re

from django.core.exceptions import ValidationError

# An optional leading slash, then one or more non-empty components separated by single slashes,
# then an optional trailing slash. No null bytes. Components can't contain "/", so there is only
# one way to split any input into them, and matching stays linear. The previous
# r"^(/)?([^/\0]+(/)?)+$" accepted the same paths but nested one quantifier in another, so a near
# miss -- a long component followed by "//" or a null byte -- backtracked exponentially.
POSIX_PATH = re.compile(r"/?[^/\0]+(?:/[^/\0]+)*/?")


def validate_posix_path(path):
    """Raise ValidationError unless `path` is a well-formed absolute or relative Unix path."""
    if not POSIX_PATH.fullmatch(path):
        raise ValidationError(
            f"Invalid path '{path}'. Path must be a valid Unix-style path and cannot contain null characters."
        )
