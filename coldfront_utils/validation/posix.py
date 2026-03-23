import re
from django.core.exceptions import ValidationError


def validate_path(path):
    # add any path validation logic here if needed, for example to ensure the path is in a valid format for the storage system or does not contain any invalid characters. For now we will just return the path as is.
    if not re.fullmatch(r'^(/)?([^/\0]+(/)?)+$', path):
        raise ValidationError(f"Invalid path '{path}'. Path must be a valid Unix-style path and cannot contain null characters.")
