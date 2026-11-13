from importlib.util import find_spec

from django import forms
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


def require_coldfront_features():
    """Raise ImproperlyConfigured unless ColdFront has what this app builds on, as from 1.1.8: an
    AllocationForm that's a ModelForm, and django-htmx. Checked by feature, not by version, so it works
    for a ColdFront run from a source checkout, which has no package metadata."""
    from coldfront.core.allocation import forms as allocation_forms

    missing = []
    if not issubclass(allocation_forms.AllocationForm, forms.ModelForm):
        missing.append("its AllocationForm is not a ModelForm")
    if find_spec("django_htmx") is None:
        missing.append("django-htmx is not installed")
    if missing:
        raise ImproperlyConfigured(
            "coldfront_utils.allocation_request needs ColdFront 1.1.8 or later: " + "; ".join(missing)
        )


class AllocationRequestConfig(AppConfig):
    """Installing this app makes ColdFront's request page use AllocationRequestForm, with its sections."""

    name = "coldfront_utils.allocation_request"
    label = "coldfront_utils_allocation_request"
    verbose_name = "Allocation request form"

    def ready(self):
        # first: the modules below import what older ColdFront doesn't have
        require_coldfront_features()

        from . import checks  # noqa: F401 -- registers them
        from .views import install_create_view

        install_create_view()
