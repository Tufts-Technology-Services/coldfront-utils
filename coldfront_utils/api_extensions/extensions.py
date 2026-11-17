"""Extensions: what a plugin serves through the REST API, and the registry of them."""

import inspect
import re

from django.core.exceptions import ImproperlyConfigured

# (kind, name) -> extension class
_registry = {}

# An extension's name is a URL segment: users/<username>/<name>/.
EXTENSION_NAME = re.compile(r"[a-z][a-z0-9-]*")


class ExtensionNotFound(Exception):
    """No such item, or not one this caller may see. The API answers 404."""


class ExtensionBadRequest(Exception):
    """The query parameters are wrong (an unknown status, say). The API answers 400 with str() of the
    exception, so say what's wrong in words the caller can act on."""


class ExtensionUnavailable(Exception):
    """The content can't be fetched right now (its storage system is down, say). The API answers 503
    with str() of the exception, so the message must be safe to show callers: log the details in the
    plugin's own logger."""


class ApiExtension:
    """Base of the three kinds of extension. Subclass PersonExtension, ProjectExtension or
    AllocationExtension, not this.

    A subclass sets the class attributes and implements allows() and summary(), and item() if it has
    items. The API makes an instance for each request. `target` is what the URL names (a User, a
    Project or an Allocation, by kind) and `caller` is the User asking. summary() and item() also get
    `params`, the request's query parameters as {name: [value, ...]} (a list, since a parameter can be
    repeated); ignore any you don't use, and raise ExtensionBadRequest for a value you can't use. They
    return data that can be rendered as JSON, and should include {"extension": name, "version":
    version}, so a caller can see what it got.

    An item's URL is its summary's URL plus <key>/, so a summary lists its items by key, not by URL:
    the extension never needs to know the host or the API version the caller used.
    """

    # The extension's URL segment: lowercase letters, digits and hyphens, starting with a letter.
    name = ""
    # One line, listed by the API's discovery endpoint.
    description = ""
    # The version of the shape summary() and item() return, a positive integer. Adding a field keeps
    # it; a breaking change is a new extension with a new name (storage-v2), the old one marked
    # deprecated until its callers have moved.
    version = 1
    # Listed by the discovery endpoint, so callers know to move to the newer name.
    deprecated = False

    def allows(self, target, caller) -> bool:
        """Whether caller may see this extension's content about target. The API answers 404 when it
        doesn't, as for a target that doesn't exist. Required: there's no default."""
        raise NotImplementedError(f"{type(self).__name__} must say who may see it")

    def summary(self, target, caller, params):
        """The extension's content about target, at <name>/."""
        raise NotImplementedError(f"{type(self).__name__} must return its summary")

    def item(self, target, key, caller, params):
        """One item, at <name>/<key>/; key is a string. Raise ExtensionNotFound for a key there's no
        item for. By default, the extension has no items."""
        raise ExtensionNotFound(key)


class PersonExtension(ApiExtension):
    """Content about a person: target is a User."""


class ProjectExtension(ApiExtension):
    """Content about a project: target is a Project."""


class AllocationExtension(ApiExtension):
    """Content about an allocation: target is an Allocation."""


# kind -> base class; the API's routes use the kinds
KINDS = {"person": PersonExtension, "project": ProjectExtension, "allocation": AllocationExtension}


def register_api_extension(extension_class):
    """Register an extension class, so the API serves it. Returns the class, so it can be used as a
    decorator. Raises ImproperlyConfigured for a class that isn't a subclass of exactly one of the
    three kinds, that doesn't implement allows() and summary(), whose name isn't a URL segment of
    lowercase letters, digits and hyphens, whose description is empty or whose version isn't a
    positive integer, or whose kind and name another class already has. Registering the same class
    again is allowed. Checked here, not by a system check, since those don't run when gunicorn or
    uWSGI start the app."""
    if not isinstance(extension_class, type):
        raise ImproperlyConfigured(f"register_api_extension takes an extension class, not {extension_class!r}")
    class_name = extension_class.__name__
    kinds = [kind for kind, base in KINDS.items() if issubclass(extension_class, base)]
    if len(kinds) != 1:
        raise ImproperlyConfigured(
            f"{class_name} must subclass one of PersonExtension, ProjectExtension or AllocationExtension: "
            "the kind says what the API's URL names, a person, a project or an allocation"
        )
    kind = kinds[0]
    for method in ("allows", "summary"):
        if getattr(extension_class, method) is getattr(ApiExtension, method):
            raise ImproperlyConfigured(f"{class_name} must implement {method}()")
    for method in ("summary", "item"):
        if not takes_params(getattr(extension_class, method)):
            raise ImproperlyConfigured(
                f"{class_name}.{method}() must take `params`, the request's query parameters: "
                f"{method}(self, target, {'key, ' if method == 'item' else ''}caller, params)"
            )
    name = extension_class.name
    if not (isinstance(name, str) and EXTENSION_NAME.fullmatch(name)):
        raise ImproperlyConfigured(
            f"{class_name}.name {name!r} must be lowercase letters, digits and hyphens, starting with a letter: "
            "it's the extension's URL segment"
        )
    if not (isinstance(extension_class.description, str) and extension_class.description):
        raise ImproperlyConfigured(f"{class_name}.description must be a line saying what the extension serves")
    version = extension_class.version
    if not (type(version) is int and version >= 1):
        raise ImproperlyConfigured(f"{class_name}.version {version!r} must be a positive integer")
    registered = _registry.get((kind, name))
    if registered is not None and registered is not extension_class:
        raise ImproperlyConfigured(
            f"{class_name} and {registered.__name__} are both {kind} extensions named {name!r}: a breaking change "
            "to an extension is a new name, such as storage-v2"
        )
    _registry[(kind, name)] = extension_class
    return extension_class


def takes_params(method):
    """Whether method can be called with params=..., so the API can pass the query parameters."""
    parameters = inspect.signature(method).parameters.values()
    return any(
        parameter.name == "params" or parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )


def find_extension(kind, name):
    """The extension class registered under kind ("person", "project" or "allocation") and name, or
    None."""
    return _registry.get((kind, name))


def registered_extensions():
    """{kind: [extension class, ...]} for every kind, each list sorted by name."""
    listed = {kind: [] for kind in KINDS}
    for (kind, name), extension_class in sorted(_registry.items()):
        listed[kind].append(extension_class)
    return listed
