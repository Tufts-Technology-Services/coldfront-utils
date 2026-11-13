"""Sample request sections and request helpers for tests/test_allocation_request.py.

Two sample sections stand in for storage's and billing's: a quota for Storage resources, and a
billing note for resources that require payment. Their card templates are in tests/templates.
"""

from contextlib import contextmanager
from decimal import Decimal

from django import forms
from django.urls import reverse

from coldfront.core.test_helpers import factories as f

from coldfront_utils.allocation_request import RequestSection, RequestSectionForm
from coldfront_utils.allocation_request.forms import SECTIONS_CONTAINER_ID, AllocationRequestForm

QUOTA_ATTRIBUTE = "Storage Quota (TB)"
billing_notes_saved = []

# the headers htmx sends when the resource drop-down asks for its cards
CARDS_REQUEST = {"HX-Request": "true", "HX-Target": SECTIONS_CONTAINER_ID}

VIEWS_LOGGER = "coldfront_utils.allocation_request.views"


def check_id(code):
    """The full id of one of the app's system check messages, e.g. check_id("E001")."""
    return f"coldfront_utils.allocation_request.{code}"


class QuotaForm(RequestSectionForm):
    quota = forms.DecimalField(label=QUOTA_ATTRIBUTE, min_value=Decimal("0.1"))


class QuotaSection(RequestSection):
    name = "test_quota"
    order = 10
    form_class = QuotaForm
    template = "test_sections/quota.html"

    def applies(self):
        return self.resource.resource_type.name == "Storage"

    def save(self, allocation, form):
        f.AllocationAttributeFactory(
            allocation=allocation,
            allocation_attribute_type=f.AllocationAttributeTypeFactory(name=QUOTA_ATTRIBUTE),
            value=str(form.cleaned_data["quota"]),
        )


class BillingNoteForm(RequestSectionForm):
    cost_center = forms.CharField(required=False)
    grant = forms.CharField(required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("grant") and not cleaned.get("cost_center"):
            self.add_error("grant", "Choose a cost center before a grant.")
        return cleaned


class BillingNoteSection(RequestSection):
    name = "test_billing"
    order = 20
    form_class = BillingNoteForm
    template = "test_sections/note.html"

    def applies(self):
        return self.resource.requires_payment

    def save(self, allocation, form):
        billing_notes_saved.append((allocation.pk, self.resource.name, form.cleaned_data["cost_center"]))

    def card_context(self):
        return {"price": "$5.00"}


class BrokenSection(QuotaSection):
    """Fails while saving, after the allocation is saved."""

    name = "test_broken"

    def save(self, allocation, form):
        raise RuntimeError("storage service down")


def requestable(name, type_name, paid=False):
    """A resource anyone can request."""
    return f.ResourceFactory(
        name=name,
        resource_type=f.ResourceTypeFactory(name=type_name),
        is_allocatable=True,
        is_available=True,
        is_public=True,
        requires_payment=paid,
    )


def managed_project(pi):
    """An Active project whose PI may request allocations on it."""
    project = f.ProjectFactory(pi=pi, status=f.ProjectStatusChoiceFactory(name="Active"))
    # ColdFront grants the request page to a project's Managers; projects it creates make the PI one
    f.ProjectUserFactory(
        project=project,
        user=pi,
        role=f.ProjectUserRoleChoiceFactory(name="Manager"),
        status=f.ProjectUserStatusChoiceFactory(name="Active"),
    )
    return project


def limit_allocations(resource, limit):
    """Give `resource` ColdFront's allocation_limit attribute."""
    limit_type = f.ResourceAttributeTypeFactory(
        name="allocation_limit", attribute_type=f.RAttributeTypeFactory(name="Int")
    )
    f.ResourceAttributeFactory(resource=resource, resource_attribute_type=limit_type, value=str(limit))


@contextmanager
def receiver_connected(signal, receiver, **connect_kwargs):
    """`receiver` connected to `signal` for the block; give a dispatch_uid."""
    signal.connect(receiver, **connect_kwargs)
    try:
        yield
    finally:
        signal.disconnect(sender=connect_kwargs.get("sender"), dispatch_uid=connect_kwargs["dispatch_uid"])


def request_form(pi, project, data=None, initial=None, files=None, request=None):
    return AllocationRequestForm(pi, project.pk, data=data, files=files, initial=initial or {}, request=request)


def request_data(project, resource, **sections_data):
    """What the request page posts for `resource`; `resource` may be a Resource or a raw value."""
    return {
        "resource": str(getattr(resource, "pk", resource)),
        "justification": "Sequencing data for the NIH R01 project.",
        "quantity": "1",
        "project": str(project.pk),
        "is_changeable": "True",
        **sections_data,
    }


def create_url(project):
    return reverse("allocation-create", args=[project.pk])


def get_cards(client, project, resource):
    """Ask the request page for `resource`'s cards, as the resource drop-down does."""
    return client.get(create_url(project), {"resource": resource.pk}, headers=CARDS_REQUEST)
