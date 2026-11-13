"""The request page's view: ColdFront's AllocationCreateView, with AllocationRequestForm."""

import logging

from django.contrib import messages
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404, HttpResponse
from django.utils.cache import patch_vary_headers
from django_htmx.http import HttpResponseClientRefresh

from coldfront.core.allocation import views as allocation_views

from .forms import SECTIONS_CONTAINER_ID, AllocationRequestForm

logger = logging.getLogger(__name__)

COLDFRONT_VIEWS_MODULE = "coldfront.core.allocation.views"
# the full page and the cards share a URL, so caches must tell them apart by these
HTMX_HEADERS = ("HX-Request", "HX-Target")


def coldfront_create_view():
    """ColdFront's own AllocationCreateView, even if an app has already replaced the module's name with
    a subclass of it."""
    for view_class in allocation_views.AllocationCreateView.__mro__:
        if view_class.__module__ == COLDFRONT_VIEWS_MODULE and view_class.__name__ == "AllocationCreateView":
            return view_class
    live = allocation_views.AllocationCreateView
    raise ImproperlyConfigured(
        f"coldfront.core.allocation.views.AllocationCreateView has been replaced with {live.__module__}."
        f"{live.__qualname__}, which isn't ColdFront's view or a subclass of it, so "
        "coldfront_utils.allocation_request can't build on it. That plugin should register a request "
        "section instead."
    )


class AllocationRequestCreateView(coldfront_create_view()):
    """ColdFront's request page with AllocationRequestForm. When the resource drop-down asks for its
    cards (an htmx GET of the same URL, targeting the cards' container, with ?resource=), it returns
    just the cards for that resource, so they pass the page's own permission checks (test_func and
    dispatch run first). Any other request gets the page."""

    form_class = AllocationRequestForm

    def dispatch(self, request, *args, **kwargs):
        if not wants_cards(request):
            return super().dispatch(request, *args, **kwargs)
        # The page's checks may refuse a request for the cards: redirect it (session expired, project
        # needs review or archived), deny it (no longer allowed) or not find the project; or building
        # the cards may fail. htmx would swap a redirect's whole destination page into the cards, and
        # swaps nothing for an error, leaving another resource's cards. So reload the page instead,
        # which shows the refusal or the error properly.
        queued = queued_messages(request)
        already_queued = len(queued)
        try:
            response = super().dispatch(request, *args, **kwargs)
        except (PermissionDenied, Http404):
            response = None
        except Exception:
            logger.exception("Couldn't render the request cards for %s", request.get_full_path())
            response = None
        if response is not None and not 300 <= response.status_code < 400:
            return response
        # the reload queues the refusal's message again, so drop the one this request queued (only
        # that: messages from earlier requests are still to be shown)
        del queued[already_queued:]
        response = HttpResponseClientRefresh()
        patch_vary_headers(response, HTMX_HEADERS)
        return response

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_initial(self):
        initial = super().get_initial()
        if "resource" in self.request.GET:
            initial["resource"] = self.request.GET["resource"]
        return initial

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if "request_sections" in form.errors:
            response.add_post_render_callback(lambda rendered: report_unshown_cards(form, rendered))
        return response

    def get(self, request, *args, **kwargs):
        if wants_cards(request):
            response = HttpResponse(self.get_form().render_request_sections())
        else:
            response = super().get(request, *args, **kwargs)
        patch_vary_headers(response, HTMX_HEADERS)
        return response


# ColdFront's own view, found once, before any other app could replace the module's name
COLDFRONT_CREATE_VIEW = AllocationRequestCreateView.__bases__[0]


def report_unshown_cards(form, response):
    """Log when a request failed on its cards but the page didn't show them, so the user saw no reason
    and nothing they could fix: the page's template is missing {{ form.request_sections }}."""
    if not form.request_sections_rendered:
        logger.error(
            "A request was refused for errors in its %s cards, but the request page (%s) doesn't render "
            "form.request_sections, so the user couldn't see or fix them. Add {{ form.request_sections }} "
            "to the template.",
            ", ".join(form.invalid_sections),
            ", ".join(response.template_name) if isinstance(response.template_name, list) else response.template_name,
        )


def queued_messages(request) -> list:
    """The messages queued during this request, not yet stored: a list that can be trimmed. Django's
    message storage keeps them in _queued_messages until the response; messages loaded from earlier
    requests are kept apart. An empty list where messages aren't installed."""
    return getattr(messages.get_messages(request), "_queued_messages", [])


def wants_cards(request) -> bool:
    """Whether this is the resource drop-down asking for its cards, rather than htmx (hx-boost, say) or
    a browser asking for the page."""
    htmx = getattr(request, "htmx", None)  # set by django_htmx's middleware, which ColdFront installs
    return bool(htmx) and htmx.target == SECTIONS_CONTAINER_ID


def install_create_view():
    """Make ColdFront's request URL use AllocationRequestCreateView. Called from the app's ready(): the
    URLconf reads the module's name when it's imported, after every app's ready().

    Raises ImproperlyConfigured if another app has already replaced the view: replacing it in turn
    would silently drop that app's view, and plugins should register a request section instead.
    """
    live = allocation_views.AllocationCreateView
    if live not in (AllocationRequestCreateView, COLDFRONT_CREATE_VIEW):
        raise ImproperlyConfigured(
            f"{live.__module__}.{live.__qualname__} has already replaced ColdFront's AllocationCreateView. "
            "coldfront_utils.allocation_request would replace it in turn, so that app's request page would "
            "never be used. That plugin should register a request section instead."
        )
    allocation_views.AllocationCreateView = AllocationRequestCreateView
