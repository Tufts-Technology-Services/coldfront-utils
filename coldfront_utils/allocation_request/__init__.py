"""One allocation request form that plugins add to.

Add "coldfront_utils.allocation_request" to INSTALLED_APPS. Its place in the list doesn't matter.
The app then makes ColdFront's request page (AllocationCreateView) use AllocationRequestForm.

A plugin adds a card to that form by subclassing RequestSection and registering it in its
AppConfig.ready() with register_request_section(). When a resource is chosen, the form shows the
card of every section that applies to it. It validates those sections with ColdFront's own fields,
and saves them with the allocation, before ColdFront sends its admin email and `allocation_new`.
So everything listening to those sees a complete request. See docs/allocation-request-plan.md in
this repository for the design.
"""

from .sections import RequestSection, RequestSectionForm, register_request_section

__all__ = ["RequestSection", "RequestSectionForm", "register_request_section"]
