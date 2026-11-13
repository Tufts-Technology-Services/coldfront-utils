"""System checks for the allocation request form."""

import re

from django.conf import settings
from django.core import checks as django_checks
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from coldfront.core.allocation import views as allocation_views
from coldfront.core.allocation.signals import allocation_activate_user, allocation_new

from . import sections
from .views import COLDFRONT_CREATE_VIEW, AllocationRequestCreateView

# The signals ColdFront's request page sends with sender=self.__class__, which is now this app's view.
REQUEST_PAGE_SIGNALS = {"allocation_new": allocation_new, "allocation_activate_user": allocation_activate_user}


@django_checks.register()
def check_section_setting(app_configs, **kwargs):
    """E001: ALLOCATION_REQUEST_SECTIONS names a section nobody registered. E003: it names one twice.
    E006: it isn't a list or tuple."""
    setting = getattr(settings, "ALLOCATION_REQUEST_SECTIONS", None)
    if setting is not None and not isinstance(setting, (list, tuple)):
        return [
            django_checks.Error(
                f"ALLOCATION_REQUEST_SECTIONS must be a list of section names, not {type(setting).__name__}",
                hint=f"For one section: ALLOCATION_REQUEST_SECTIONS = [{setting!r}]",
                id="coldfront_utils.allocation_request.E006",
            )
        ]
    chosen = sections.chosen_section_names() or []
    errors = []
    unknown = [name for name in chosen if name not in sections._registry]
    if unknown:
        errors.append(
            django_checks.Error(
                f"ALLOCATION_REQUEST_SECTIONS names sections nothing registered: {', '.join(unknown)}",
                hint=f"Registered sections: {', '.join(sections._registry) or 'none'}. Is the plugin installed?",
                id="coldfront_utils.allocation_request.E001",
            )
        )
    twice = sorted({name for name in chosen if chosen.count(name) > 1})
    if twice:
        errors.append(
            django_checks.Error(
                f"ALLOCATION_REQUEST_SECTIONS names sections more than once: {', '.join(twice)}",
                hint="Each section's card appears once; list each name once, where it should go.",
                id="coldfront_utils.allocation_request.E003",
            )
        )
    return errors


def request_page_view_class():
    """The view class ColdFront's request URL actually resolves to."""
    return resolve(reverse("allocation-create", args=[1])).func.view_class


def create_view_problem():
    """What keeps ColdFront's request page from using this app's view, or None if nothing does."""
    live = allocation_views.AllocationCreateView
    if live is not AllocationRequestCreateView:
        return f"coldfront.core.allocation.views.AllocationCreateView is {live.__module__}.{live.__qualname__}"
    try:
        url_view = request_page_view_class()
    except (NoReverseMatch, Resolver404, AttributeError) as e:
        return f"the allocation-create URL couldn't be resolved to a view class ({e})"
    if url_view is not AllocationRequestCreateView:
        return (
            f"the allocation-create URL uses {url_view.__module__}.{url_view.__qualname__} (the URLconf "
            "was imported before this app's ready() replaced the view)"
        )
    return None


@django_checks.register()
def check_create_view(app_configs, **kwargs):
    """E002: ColdFront's request page doesn't use this app's view, so it wouldn't show the sections:
    another app replaced AllocationCreateView after this one, or the URLconf was imported before the
    replacement and kept ColdFront's view."""
    problem = create_view_problem()
    if problem is None:
        return []
    return [
        django_checks.Error(
            f"ColdFront's request page doesn't use the allocation request form's view, so plugins' sections "
            f"won't appear on it: {problem}",
            hint="Plugins should register a request section instead of replacing AllocationCreateView, and "
            "nothing should import ColdFront's URLconf from an app's ready().",
            id="coldfront_utils.allocation_request.E002",
        )
    ]


@django_checks.register()
def check_sender_filters(app_configs, **kwargs):
    """E004: a receiver of a signal ColdFront's request page sends filters on sender= ColdFront's own
    AllocationCreateView. The page now sends them with this app's view as sender, so it never fires."""
    coldfront_view_id = id(COLDFRONT_CREATE_VIEW)
    errors = []
    for signal_name, signal in REQUEST_PAGE_SIGNALS.items():
        # each receiver is stored under (id of the receiver, id of the sender it filters on)
        if any(entry[0][1] == coldfront_view_id for entry in signal.receivers):
            errors.append(
                django_checks.Error(
                    f"A receiver of {signal_name} filters on sender=ColdFront's AllocationCreateView. With "
                    "coldfront_utils.allocation_request installed the request page sends it with its own view "
                    "as sender, so that receiver never runs.",
                    hint="Take any sender and check it with coldfront_utils.signals.sent_by(sender, "
                    "AllocationCreateView) in the receiver.",
                    id="coldfront_utils.allocation_request.E004",
                )
            )
    return errors


# what a request page template has to contain to show the cards: the field itself, or the whole form
# (crispy, {{ form }}, form.as_div and the like, or a loop over its fields)
# Django's comments: {# ... #} on one line only (over several lines it's ordinary text, and renders),
# and {% comment %} blocks
TEMPLATE_COMMENTS = re.compile(r"{#[^\n]*?#}|{%\s*comment\b.*?%}.*?{%\s*endcomment\s*%}", re.S)
SHOWS_THE_CARDS = re.compile(
    r"form\.request_sections"
    r"|\bform\s*\|\s*crispy|{%\s*crispy\s+form\b"
    r"|{{\s*form\s*}}|\bform\.as_(div|p|table|ul)\b"
    r"|{%\s*for\s+\w+\s+in\s+form(\.visible_fields)?\s*%}"
)
# a template that includes others may render the cards in one of them, which this check can't follow
INCLUDES = re.compile(r"{%\s*include\b")


@django_checks.register()
def check_request_page(app_configs, **kwargs):
    """W002: sections are registered, but the request page's template doesn't appear to render the
    cards, so a request that needs one can't be completed and the user isn't told why."""
    if not sections.active_section_classes():
        return []
    name = AllocationRequestCreateView.template_name
    try:
        source = get_template(name).template.source
    except TemplateDoesNotExist:
        return []
    source = TEMPLATE_COMMENTS.sub("", source)
    if SHOWS_THE_CARDS.search(source) or INCLUDES.search(source):
        return []
    return [
        django_checks.Warning(
            f"The request page's template ({name}) doesn't appear to render the request sections' cards, so "
            "requests for resources that need one can't be completed, and the page won't say why.",
            hint="Add {{ form.request_sections }} to the template, after the request's own fields.",
            id="coldfront_utils.allocation_request.W002",
        )
    ]


@django_checks.register()
def check_sections(app_configs, **kwargs):
    """E005: a section in use can't build its form or render its card: its form_class isn't a
    RequestSectionForm, or its template is missing. Every request for its resources would fail."""
    errors = []
    for section_class in sections.active_section_classes():
        form_class, template = section_class.form_class, section_class.template
        problems = []
        if not (isinstance(form_class, type) and issubclass(form_class, sections.RequestSectionForm)):
            problems.append("its form_class isn't a RequestSectionForm subclass")
        if not template:
            problems.append("it has no template")
        else:
            try:
                get_template(template)
            except TemplateDoesNotExist:
                problems.append(f"its template {template!r} doesn't exist")
        if problems:
            errors.append(
                django_checks.Error(
                    f"Request section {section_class.name!r} ({section_class.__qualname__}) can't be shown: "
                    + "; ".join(problems),
                    hint="Requests for the resources it applies to would fail with a server error.",
                    id="coldfront_utils.allocation_request.E005",
                )
            )
    return errors
