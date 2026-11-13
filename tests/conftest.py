import pytest

from coldfront.core.test_helpers import factories as f

from coldfront_utils.allocation_request import register_request_section, sections, views
from coldfront_utils.allocation_request.views import AllocationRequestCreateView

from .request_section_samples import BillingNoteSection, QuotaSection, billing_notes_saved, managed_project, requestable


@pytest.fixture
def fresh_registry(monkeypatch):
    """An empty request section registry, so each test registers its own sections."""
    monkeypatch.setattr(sections, "_registry", {})
    billing_notes_saved.clear()


@pytest.fixture
def both_sections(fresh_registry):
    """The sample quota and billing sections, registered."""
    register_request_section(QuotaSection)
    register_request_section(BillingNoteSection)


@pytest.fixture
def pi():
    return f.UserFactory(username="asmith02")


@pytest.fixture
def project(pi):
    """An Active project whose PI may request allocations on it."""
    project = managed_project(pi)
    f.AllocationStatusChoiceFactory(name="New")
    f.AllocationUserStatusChoiceFactory(name="Active")
    return project


@pytest.fixture
def fresh_unshown_card_reports(monkeypatch):
    """Forget which pages were already reported for not showing the cards (once per process)."""
    monkeypatch.setattr(views, "_pages_reported_without_cards", set())


@pytest.fixture
def page_without_cards(monkeypatch):
    """The request page's template replaced with one that doesn't render the cards."""
    monkeypatch.setattr(AllocationRequestCreateView, "template_name", "test_pages/without_sections.html")


@pytest.fixture
def paid_storage():
    return requestable("Tier 1 HPC", "Storage", paid=True)


@pytest.fixture
def free_storage():
    return requestable("Course - Tier 1 HPC", "Storage")


@pytest.fixture
def cluster():
    return requestable("Research Cluster", "Cluster")
