"""Sections: what a plugin adds to the allocation request form, and the registry of them."""

import re

from django import forms
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# section name -> RequestSection subclass, in registration order
_registry = {}

# A section's name is its form's prefix, so it ends up in element ids: no dots (read as a class in
# CSS selectors and jQuery) and no hyphens (Django puts one between prefix and field name).
SECTION_NAME = re.compile(r"[a-z][a-z0-9_]*")


class RequestSectionForm(forms.Form):
    """Base for a section's form. It is built with the request's project, user and chosen resource, so
    its clean() can check fields against them, and with the section's name as its prefix."""

    def __init__(self, *args, project, user, resource, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.user = user
        self.resource = resource


class RequestSection:
    """A plugin's part of the allocation request form: one card, shown for the resources it applies to.

    Subclass it, set the class attributes, implement applies() and save(), and register the class
    with register_request_section() in the plugin's AppConfig.ready().

    The form builds a section for each request, with that request's details: `resource` (the chosen
    one), `project`, `user` (who is requesting) and `request` (the HttpRequest, or None outside a
    request). The methods read them from self, and may keep what they look up on self for the rest of
    the request, e.g. an account found in applies() and saved in save().
    """

    # A stable identifier, also the section form's prefix: lowercase letters, digits and underscores.
    name = ""
    # Where the card goes among the sections; lower comes first.
    order = 100
    # A RequestSectionForm subclass with the section's fields.
    form_class = RequestSectionForm
    # The card's template. It extends "allocation_request/card.html".
    template = ""

    def __init__(self, *, resource, project, user, request=None):
        self.resource = resource
        self.project = project
        self.user = user
        self.request = request

    def applies(self) -> bool:
        """Whether this request (for self.resource on self.project, by self.user) needs this section."""
        raise NotImplementedError(f"{type(self).__name__} must say which resources it applies to")

    def save(self, allocation, form) -> None:
        """Save this section's part of the request from its valid `form`.

        Runs after the allocation is saved and before ColdFront attaches the resource, so use
        self.resource, not allocation.resources. Mustn't fail on anything a user or admin can get
        wrong: check that in the form or with a system check.

        It runs inside a transaction with the allocation, which a later section can still roll back.
        Don't queue tasks from save(). The transaction covers the allocation and the sections only:
        when it commits, ColdFront hasn't yet attached the resource, added the PI and users or sent
        allocation_new. Work that needs the finished request belongs in a receiver of allocation_new
        (check its sender with coldfront_utils.signals.sent_by), queued with
        transaction.on_commit(lambda: async_task(...)) where it might run inside a transaction.
        """
        raise NotImplementedError(f"{type(self).__name__} must save its part of the request")

    def card_context(self) -> dict:
        """Extra context for the card's template."""
        return {}

    def build_form(self, data, files=None) -> RequestSectionForm:
        """This section's form, bound to `data` (and uploaded `files`) if it isn't None. A section with a
        file field needs the request page's form to have enctype="multipart/form-data"."""
        return self.form_class(
            data=data, files=files, prefix=self.name, project=self.project, user=self.user, resource=self.resource
        )


def register_request_section(section_class):
    """Register a RequestSection subclass, so its card appears on the request form. Returns the class,
    so it can be used as a decorator. Raises ImproperlyConfigured for a name that isn't an identifier
    of lowercase letters, digits and underscores, or that another section already has, and for a
    form_class that isn't a RequestSectionForm subclass. Checked here, not by a system check, since
    those don't run when gunicorn or uWSGI start the app."""
    name = section_class.name
    if not SECTION_NAME.fullmatch(name or ""):
        raise ImproperlyConfigured(
            f"{section_class.__name__}.name {name!r} must be lowercase letters, digits and underscores, "
            "starting with a letter: it's the section's form prefix, so it ends up in element ids"
        )
    form_class = section_class.form_class
    if not (isinstance(form_class, type) and issubclass(form_class, RequestSectionForm)):
        raise ImproperlyConfigured(
            f"{section_class.__name__}.form_class must be a RequestSectionForm subclass, not {form_class!r}: "
            "the request form builds it with the request's project, user and resource"
        )
    registered = _registry.get(name)
    if registered is not None and registered is not section_class:
        raise ImproperlyConfigured(
            f"{section_class.__name__} and {registered.__name__} are both request sections named {name!r}"
        )
    _registry[name] = section_class
    return section_class


def chosen_section_names():
    """The section names the ALLOCATION_REQUEST_SECTIONS setting gives, in its order, or None if it isn't
    set. One name without its brackets is that name; anything else that isn't a list or tuple gives
    none. The system checks report those, and names listed twice or not registered."""
    chosen = getattr(settings, "ALLOCATION_REQUEST_SECTIONS", None)
    if chosen is None or isinstance(chosen, (list, tuple)):
        return chosen
    return [chosen] if isinstance(chosen, str) else []


def active_section_classes() -> list:
    """The classes of the sections the request form uses, in order.

    That's the sections ALLOCATION_REQUEST_SECTIONS names, in its order, if it's set: names that
    aren't registered are left out, and a name listed twice is used once. Otherwise, every registered
    section, by `order` and then registration.
    """
    chosen = chosen_section_names()
    if chosen is not None:
        # dict.fromkeys keeps the first of a name listed twice
        return [_registry[name] for name in dict.fromkeys(chosen) if name in _registry]
    # sorted() is stable, so sections with the same order stay in registration order
    return sorted(_registry.values(), key=lambda section_class: section_class.order)
