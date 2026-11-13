"""AllocationRequestForm: ColdFront's request form, with the cards of the sections that apply."""

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

from coldfront.core.allocation.forms import AllocationForm
from coldfront.core.allocation.models import AllocationStatusChoice

from .sections import active_section_classes

# The element htmx swaps the cards into, and the error that says a card has something wrong.
SECTIONS_CONTAINER_ID = "allocation-request-sections"
SECTION_ERRORS_MESSAGE = "Please correct the errors below."


class RequestSectionsWidget(forms.Widget):
    """Takes no input. RequestSectionsBoundField renders the cards in its place."""

    def value_from_datadict(self, data, files, name):
        return None


class RequestSectionsBoundField(forms.BoundField):
    """Renders the container with the cards of the sections that apply to the form's resource.

    Shows no errors of its own. The summary error lives on this field, so it's in form.errors and the
    form is invalid, but the container shows it, above the cards; crispy would print it again."""

    @property
    def errors(self):
        return self.form.error_class(renderer=self.form.renderer)

    def as_widget(self, widget=None, attrs=None, only_initial=False):
        self.form.request_sections_rendered = True
        return self.form.render_request_sections()  # rendered by a template, so already safe


class RequestSectionsField(forms.Field):
    """The cards' place in the form. A page that renders the whole form gets it with ColdFront's fields;
    a page that renders fields by hand adds {{ form.request_sections }}."""

    widget = RequestSectionsWidget

    def __init__(self, **kwargs):
        super().__init__(required=False, label="", **kwargs)

    def get_bound_field(self, form, field_name):
        return RequestSectionsBoundField(form, self, field_name)


class AllocationRequestForm(AllocationForm):
    """ColdFront's AllocationForm, plus a form for each section that applies to the chosen resource.

    The sections' forms aren't part of this form's fields: they're in `section_forms`, rendered only
    in their cards. clean() validates them with ColdFront's fields, and save() saves them with the
    allocation, in one transaction (the allocation and the sections only: ColdFront's view then attaches
    the resource, adds users and sends allocation_new after it). The project is the one the page was
    built for, whatever is posted. `request`, if given, renders the cards with the site's context
    processors, as the page around them is.
    """

    request_sections = RequestSectionsField()

    def __init__(self, request_user, project_pk, *args, request=None, **kwargs):
        super().__init__(request_user, project_pk, *args, **kwargs)
        self.request = request
        self.project = self.initial["project"]  # AllocationForm puts the Project there
        # The allocation goes to the project in the URL, the one the page checked permission on and the
        # sections ran for. A disabled field takes its initial value and ignores what's posted.
        self.fields["project"].disabled = True
        # set when a page renders the cards, so the view can tell a page that never shows them
        self.request_sections_rendered = False
        self.invalid_sections = []
        # changing the resource asks the request page (the same view) for that resource's cards
        self.fields["resource"].widget.attrs.update(
            {
                "hx-get": reverse("allocation-create", args=[project_pk]),
                "hx-trigger": "change",
                "hx-target": f"#{SECTIONS_CONTAINER_ID}",
                "hx-swap": "outerHTML",
                # a browser restoring another choice on reload would show it with the cards rendered
                # for this one, and htmx hears no change
                "autocomplete": "off",
            }
        )
        # the resource the sections are for, worked out once
        self.resource = self._find_resource()
        data = self.data if self.is_bound else None
        files = self.files if self.is_bound else None
        if self.resource is None:
            # No valid resource, posted or initial: the drop-down shows its first option, so show that
            # resource's cards. Unfilled, if one was posted: the resource field's error is the one to
            # fix, not the cards'.
            self.resource = self.fields["resource"].queryset.first()
            data = files = None
        self.sections = []
        self.section_forms = {}
        if self.resource is not None:
            for section_class in active_section_classes():
                section = section_class(
                    resource=self.resource, project=self.project, user=request_user, request=request
                )
                if section.applies():
                    self.sections.append(section)
                    self.section_forms[section.name] = section.build_form(data, files=files)

    def _find_resource(self):
        """The posted resource if bound, else the initial one, as ColdFront's resource field accepts it
        (so " 5" is resource 5, as the form saves it), or None if the field refuses it (or there's none:
        the field is required)."""
        if self.is_bound:
            value = self.data.get(self.add_prefix("resource"))
        else:
            value = self.initial.get("resource")
        try:
            return self.fields["resource"].clean(value)
        except ValidationError:
            return None

    def render_request_sections(self) -> str:
        """The container with the applicable sections' cards, as HTML, with the summary error above them
        once a submit has found one."""
        cards = []
        for section in self.sections:
            context = {
                **section.card_context(),
                "section": section,
                "section_form": self.section_forms[section.name],
                "resource": self.resource,
                "project": self.project,
            }
            cards.append(render_to_string(section.template, context, request=self.request))
        summary = self.errors.get("request_sections")  # None until a submit is checked
        return render_to_string(
            "allocation_request/sections.html",
            {"container_id": SECTIONS_CONTAINER_ID, "cards": cards, "summary_errors": summary},
            request=self.request,
        )

    def clean(self):
        try:
            if "resource" not in self.cleaned_data or "project" not in self.cleaned_data:
                # ColdFront's own checks need both (they'd crash without them); the field's error says
                # what's wrong
                return self.cleaned_data
            return super().clean()
        finally:
            self._hold_status_for_validation()
            # even when ColdFront's own checks raise, so the page shows every card's errors at once
            self._check_sections()

    def _hold_status_for_validation(self):
        """ColdFront's clean() sets the new allocation's status last, so when it refuses the request
        (the allocation limit, a missing account) or is skipped, there's none. Django still runs
        Allocation.clean(), which reads the status, and would crash with a 500 instead of showing the
        form's errors. A status that's never saved stands in: the form is invalid, so nothing is."""
        if self.instance.status_id is None:
            self.instance.status = AllocationStatusChoice(name="New")

    def _check_sections(self):
        # names of the sections whose forms are invalid, for the summary error and the view's log
        self.invalid_sections = [
            name for name, form in self.section_forms.items() if form.is_bound and not form.is_valid()
        ]
        if self.invalid_sections:
            self.add_error("request_sections", SECTION_ERRORS_MESSAGE)

    def save(self, commit=True):
        """Save the allocation and each section's part, in one transaction.

        With commit=False nothing is saved yet, as Django's forms do: the caller saves the allocation
        and then calls save_m2m(), which also saves the sections (outside any transaction this opens:
        wrap the caller's own steps in one if they must succeed together).
        """
        if not commit:
            allocation = super().save(commit=False)
            save_many_to_many = self.save_m2m

            def save_m2m_and_sections():
                save_many_to_many()
                self._save_sections(allocation)

            self.save_m2m = save_m2m_and_sections
            return allocation
        with transaction.atomic():
            allocation = super().save(commit=True)
            self._save_sections(allocation)
        return allocation

    def _save_sections(self, allocation):
        for section in self.sections:
            section.save(allocation, self.section_forms[section.name])
