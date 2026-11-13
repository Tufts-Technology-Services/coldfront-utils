"""allocation_request: plugins' sections on ColdFront's own allocation request form.

The sample sections, a quota for Storage resources and a billing note for resources that require
payment, are in tests/request_section_samples.py; the fixtures are in tests/conftest.py.
"""

import importlib.metadata
import logging

import pytest
from django import forms
from django.contrib.messages import get_messages
from django.core import checks as django_checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import NoReverseMatch

from coldfront.core.allocation import forms as allocation_forms
from coldfront.core.allocation import views as allocation_views
from coldfront.core.allocation.forms import AllocationForm
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.allocation.signals import allocation_activate_user, allocation_new
from coldfront.core.test_helpers import factories as f

from coldfront_utils.allocation_request import RequestSectionForm, apps, checks, register_request_section, sections
from coldfront_utils.allocation_request.forms import SECTION_ERRORS_MESSAGE, SECTIONS_CONTAINER_ID
from coldfront_utils.allocation_request.views import (
    AllocationRequestCreateView,
    coldfront_create_view,
    install_create_view,
)
from coldfront_utils.signals import sent_by

from .request_section_samples import (
    CARDS_REQUEST,
    QUOTA_ATTRIBUTE,
    VIEWS_LOGGER,
    BrokenSection,
    QuotaSection,
    billing_notes_saved,
    check_id,
    create_url,
    get_cards,
    limit_allocations,
    managed_project,
    receiver_connected,
    request_data,
    request_form,
    requestable,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("fresh_registry")]


def receiver_that_does_nothing(sender, **kwargs):
    pass


# -- The registry and the setting -----------------------------------------------------------------


def test_registered_sections_are_used_by_order(both_sections):
    register_request_section(type("EarlySection", (QuotaSection,), {"name": "test_early", "order": 1}))

    assert [s.name for s in sections.active_section_classes()] == ["test_early", "test_quota", "test_billing"]


@pytest.mark.parametrize(
    ("setting", "names"),
    [
        (["test_billing"], ["test_billing"]),
        (["test_quota", "test_billing", "test_quota"], ["test_quota", "test_billing"]),  # listed twice: used once
        ("test_quota", ["test_quota"]),  # one name without its brackets still means that section
        # not a list of names: none (the system check reports these)
        (5, []),
        ({"test_quota": True}, []),
        ("", []),
        (0, []),
    ],
)
def test_the_setting_chooses_and_orders_the_sections(both_sections, settings, setting, names):
    settings.ALLOCATION_REQUEST_SECTIONS = setting

    assert [s.name for s in sections.active_section_classes()] == names


def test_registering_returns_the_class_so_it_can_decorate():
    assert register_request_section(QuotaSection) is QuotaSection


def test_registering_the_same_class_again_is_harmless():
    register_request_section(QuotaSection)
    register_request_section(QuotaSection)

    assert [s.name for s in sections.active_section_classes()] == ["test_quota"]


@pytest.mark.parametrize("name", ["storage.quota", "Storage", "", "2quota", "quota-1"])
def test_a_name_that_isnt_a_lowercase_identifier_is_refused(name):
    with pytest.raises(ImproperlyConfigured):
        register_request_section(type("Badly", (QuotaSection,), {"name": name}))


def test_two_sections_cant_share_a_name():
    register_request_section(QuotaSection)

    with pytest.raises(ImproperlyConfigured):
        register_request_section(type("Imposter", (QuotaSection,), {}))


# -- The form and its resource --------------------------------------------------------------------


def test_sections_get_their_own_forms_not_fields_of_the_request_form(both_sections, pi, project, paid_storage):
    form = request_form(pi, project, initial={"resource": paid_storage.pk})

    assert list(form.section_forms) == ["test_quota", "test_billing"]
    assert "test_quota-quota" not in form.fields and "quota" not in form.fields
    assert list(form.fields)[-1] == "request_sections"
    assert form.section_forms["test_quota"].prefix == "test_quota"


def test_only_the_sections_that_apply_to_the_resource_get_forms(both_sections, pi, project, free_storage, cluster):
    assert list(request_form(pi, project, initial={"resource": free_storage.pk}).section_forms) == ["test_quota"]
    assert list(request_form(pi, project, initial={"resource": cluster.pk}).section_forms) == []


def test_an_unbound_form_without_a_resource_uses_the_first_the_user_can_request(both_sections, pi, project, cluster):
    # ColdFront orders the drop-down by name, so the cluster comes first
    requestable("Tier 2 Research", "Storage", paid=True)

    form = request_form(pi, project)

    assert form.resource == cluster
    assert form.section_forms == {}


def test_each_section_is_built_with_the_requests_details(both_sections, pi, project, paid_storage, rf):
    request = rf.get(create_url(project))

    form = request_form(pi, project, initial={"resource": paid_storage.pk}, request=request)

    for section in form.sections:
        assert (section.resource, section.project, section.user, section.request) == (
            paid_storage,
            project,
            pi,
            request,
        )


class AccountLookupSection(QuotaSection):
    """Looks its account up once, in applies(), and uses it again when saving."""

    name = "test_account"
    saved_accounts = []

    def applies(self):
        self.account = f"account-for-{self.project.pk}"
        return True

    def save(self, allocation, form):
        self.saved_accounts.append(self.account)


def test_a_section_keeps_what_it_looks_up_for_the_rest_of_the_request(pi, project, free_storage):
    register_request_section(AccountLookupSection)
    AccountLookupSection.saved_accounts.clear()
    form = request_form(pi, project, data=request_data(project, free_storage, **{"test_account-quota": "10"}))
    assert form.is_valid(), form.errors

    form.save()

    assert AccountLookupSection.saved_accounts == [f"account-for-{project.pk}"]


def test_each_request_gets_its_own_sections(both_sections, pi, project, paid_storage):
    first = request_form(pi, project, initial={"resource": paid_storage.pk})
    second = request_form(pi, project, initial={"resource": paid_storage.pk})

    assert not set(map(id, first.sections)) & set(map(id, second.sections))


def test_section_forms_get_the_request(both_sections, pi, project, paid_storage):
    quota_form = request_form(pi, project, initial={"resource": paid_storage.pk}).section_forms["test_quota"]

    assert (quota_form.project, quota_form.user, quota_form.resource) == (project, pi, paid_storage)


def test_a_bound_form_takes_its_sections_from_the_posted_resource(both_sections, pi, project, paid_storage, cluster):
    form = request_form(pi, project, data=request_data(project, cluster), initial={"resource": paid_storage.pk})

    assert form.section_forms == {}


def test_a_resource_the_user_cant_request_gets_no_sections(both_sections, pi, project):
    hidden = f.ResourceFactory(
        name="Secret Storage", resource_type=f.ResourceTypeFactory(name="Storage"), is_public=False
    )

    form = request_form(pi, project, initial={"resource": hidden.pk})

    assert form.resource is None and form.section_forms == {}


@pytest.mark.parametrize("posted", [" {pk}", "{pk} ", "+{pk}"])
def test_a_resource_coldfront_accepts_gets_its_sections(both_sections, pi, project, paid_storage, posted):
    # ColdFront's own field accepts these, so the sections must apply to them too
    form = request_form(pi, project, data=request_data(project, posted.format(pk=paid_storage.pk)))

    assert form.resource == paid_storage
    assert not form.is_valid()
    assert "quota" in form.section_forms["test_quota"].errors


@pytest.mark.parametrize("posted", ["999", "abc", ""])
def test_a_resource_that_isnt_requestable_is_a_form_error_not_a_crash(both_sections, pi, project, paid_storage, posted):
    form = request_form(pi, project, data=request_data(project, posted))

    assert not form.is_valid()
    assert "resource" in form.errors and "request_sections" not in form.errors
    # the page comes back showing the drop-down's first option, so its cards come back too, empty
    assert form.resource == paid_storage
    assert list(form.section_forms) == ["test_quota", "test_billing"]
    assert not any(section_form.is_bound for section_form in form.section_forms.values())


@pytest.mark.parametrize("initial", ["abc", "999"])
def test_a_bad_resource_in_the_url_gets_the_cards_the_drop_down_shows(both_sections, pi, project, initial):
    # the drop-down falls back to its first option, so the cards must too
    first = requestable("Archive Storage", "Storage")

    form = request_form(pi, project, initial={"resource": initial})

    assert form.resource == first
    assert list(form.section_forms) == ["test_quota"]


def test_the_resource_drop_down_asks_the_page_for_its_cards(pi, project):
    attrs = request_form(pi, project).fields["resource"].widget.attrs

    assert attrs["hx-get"] == create_url(project)
    assert attrs["hx-trigger"] == "change"
    assert attrs["hx-target"] == f"#{SECTIONS_CONTAINER_ID}"


def test_the_browser_doesnt_restore_another_resource_than_the_cards_are_for(pi, project):
    assert request_form(pi, project).fields["resource"].widget.attrs["autocomplete"] == "off"


def test_the_allocation_goes_to_the_project_in_the_url(both_sections, pi, project, paid_storage):
    other = f.ProjectFactory(status=f.ProjectStatusChoiceFactory(name="Active"))
    data = {**request_data(project, paid_storage, **{"test_quota-quota": "10"}), "project": str(other.pk)}

    form = request_form(pi, project, data=data)

    assert form.is_valid(), form.errors
    assert form.save().project == project


@pytest.mark.parametrize("posted_project", ["999999", ""])
def test_a_bad_posted_project_is_ignored_not_a_crash(both_sections, pi, project, paid_storage, posted_project):
    limit_allocations(paid_storage, 5)
    data = {**request_data(project, paid_storage, **{"test_quota-quota": "10"}), "project": posted_project}

    form = request_form(pi, project, data=data)

    assert form.is_valid(), form.errors


class AgreementForm(RequestSectionForm):
    agreement = forms.FileField()


class AgreementSection(QuotaSection):
    name = "test_agreement"
    form_class = AgreementForm

    def save(self, allocation, form):
        pass


def test_a_section_form_gets_the_uploaded_files(pi, project, free_storage):
    register_request_section(AgreementSection)
    upload = SimpleUploadedFile("agreement.pdf", b"%PDF-1.4 signed")

    form = request_form(
        pi, project, data=request_data(project, free_storage), files={"test_agreement-agreement": upload}
    )

    assert form.is_valid(), form.errors
    assert form.section_forms["test_agreement"].cleaned_data["agreement"].name == "agreement.pdf"


# -- Validation -------------------------------------------------------------------------------------


def test_an_invalid_section_makes_the_request_invalid(both_sections, pi, project, paid_storage):
    form = request_form(pi, project, data=request_data(project, paid_storage, **{"test_quota-quota": "0"}))

    assert not form.is_valid()
    assert form.errors["request_sections"] == [SECTION_ERRORS_MESSAGE]
    assert "quota" in form.section_forms["test_quota"].errors


def test_a_missing_section_value_makes_the_request_invalid(both_sections, pi, project, free_storage):
    form = request_form(pi, project, data=request_data(project, free_storage))

    assert not form.is_valid()
    assert "quota" in form.section_forms["test_quota"].errors


def test_a_section_form_can_check_fields_against_each_other(both_sections, pi, project, paid_storage):
    data = request_data(project, paid_storage, **{"test_quota-quota": "10", "test_billing-grant": "NIH R01"})

    form = request_form(pi, project, data=data)

    assert not form.is_valid()
    assert form.section_forms["test_billing"].errors["grant"] == ["Choose a cost center before a grant."]


def test_the_sections_are_checked_even_when_coldfronts_own_checks_fail(
    both_sections, pi, project, paid_storage, monkeypatch
):
    def at_the_limit(self):
        # ColdFront's own raises before setting the status, and Django's model validation then fails on
        # the missing status (a ColdFront bug); set it here, so this tests only the sections' part
        self.instance.status = f.AllocationStatusChoiceFactory(name="New")
        raise ValidationError("Your project is at the allocation limit allowed for this resource.")

    monkeypatch.setattr(AllocationForm, "clean", at_the_limit)
    form = request_form(pi, project, data=request_data(project, paid_storage, **{"test_quota-quota": "0"}))

    assert not form.is_valid()
    assert form.non_field_errors() == ["Your project is at the allocation limit allowed for this resource."]
    assert form.errors["request_sections"] == [SECTION_ERRORS_MESSAGE]


def test_coldfronts_allocation_limit_is_a_form_error_not_a_crash(both_sections, pi, project, paid_storage):
    limit_allocations(paid_storage, 1)
    existing = f.AllocationFactory(project=project, status=f.AllocationStatusChoiceFactory(name="New"))
    existing.resources.add(paid_storage)

    form = request_form(pi, project, data=request_data(project, paid_storage, **{"test_quota-quota": "10"}))

    assert not form.is_valid()
    assert form.non_field_errors() == ["Your project is at the allocation limit allowed for this resource."]


def test_values_for_sections_that_dont_apply_are_ignored(both_sections, pi, project, cluster):
    form = request_form(pi, project, data=request_data(project, cluster, **{"test_quota-quota": "nonsense"}))

    assert form.is_valid(), form.errors
    form.save()
    assert not AllocationAttribute.objects.exists()


# -- Saving -----------------------------------------------------------------------------------------


def test_a_valid_request_saves_the_allocation_then_its_sections(both_sections, pi, project, paid_storage):
    form = request_form(
        pi,
        project,
        data=request_data(project, paid_storage, **{"test_quota-quota": "10", "test_billing-cost_center": "Biology"}),
    )

    assert form.is_valid(), form.errors
    allocation = form.save()

    assert allocation.allocationattribute_set.get(allocation_attribute_type__name=QUOTA_ATTRIBUTE).value == "10"
    assert billing_notes_saved == [(allocation.pk, "Tier 1 HPC", "Biology")]


def test_a_section_that_fails_while_saving_saves_nothing(pi, project, paid_storage):
    register_request_section(BrokenSection)
    form = request_form(pi, project, data=request_data(project, paid_storage, **{"test_broken-quota": "10"}))
    assert form.is_valid(), form.errors

    with pytest.raises(RuntimeError):
        form.save()

    assert not Allocation.objects.exists()


def test_save_without_commit_saves_the_sections_with_save_m2m(both_sections, pi, project, paid_storage):
    form = request_form(pi, project, data=request_data(project, paid_storage, **{"test_quota-quota": "10"}))
    assert form.is_valid(), form.errors

    allocation = form.save(commit=False)
    assert not AllocationAttribute.objects.exists()
    allocation.save()
    form.save_m2m()

    assert allocation.allocationattribute_set.get(allocation_attribute_type__name=QUOTA_ATTRIBUTE).value == "10"


# -- The cards --------------------------------------------------------------------------------------


def test_the_cards_render_in_a_container_with_their_headings_and_fields(both_sections, pi, project, paid_storage):
    html = request_form(pi, project, initial={"resource": paid_storage.pk}).render_request_sections()

    assert f'id="{SECTIONS_CONTAINER_ID}"' in html
    assert 'data-request-section="test_quota"' in html and 'data-request-section="test_billing"' in html
    assert html.index("Storage") < html.index("Billing")
    assert 'name="test_quota-quota"' in html
    assert "Charged at $5.00 per TB." in html  # the section's context()


def test_rendering_the_cards_doesnt_look_the_resource_up_again(
    both_sections, pi, project, paid_storage, django_assert_num_queries
):
    form = request_form(pi, project, initial={"resource": paid_storage.pk})

    with django_assert_num_queries(0):
        form.render_request_sections()


def test_without_sections_the_container_is_still_there_for_htmx(pi, project, cluster):
    html = request_form(pi, project, initial={"resource": cluster.pk}).render_request_sections()

    assert f'id="{SECTIONS_CONTAINER_ID}"' in html
    assert "data-request-section" not in html


def test_the_summary_error_shows_above_the_cards(both_sections, pi, project, paid_storage):
    form = request_form(pi, project, data=request_data(project, paid_storage, **{"test_quota-quota": "0"}))
    form.is_valid()

    html = form.render_request_sections()

    alert = html.index("alert alert-danger")
    assert html.index(SECTION_ERRORS_MESSAGE) > alert
    assert alert < html.index("data-request-section")
    assert "invalid-feedback" not in html[: html.index("data-request-section")]


def test_no_summary_error_before_submitting(both_sections, pi, project, paid_storage):
    html = request_form(pi, project, initial={"resource": paid_storage.pk}).render_request_sections()

    assert "alert-danger" not in html


def test_the_intro_is_wrapped_so_it_can_hold_paragraphs(both_sections, pi, project, paid_storage):
    html = request_form(pi, project, initial={"resource": paid_storage.pk}).render_request_sections()

    assert '<div class="text-muted mb-3">' in html
    assert '<p class="text-muted">' not in html


def test_a_cards_errors_show_at_its_fields(both_sections, pi, project, paid_storage):
    form = request_form(pi, project, data=request_data(project, paid_storage, **{"test_quota-quota": "0"}))
    form.is_valid()

    html = form.render_request_sections()

    assert 'value="0"' in html  # what the user typed comes back
    assert "Ensure this value is greater than or equal to 0.1" in html


# -- The page ---------------------------------------------------------------------------------------


def test_the_page_shows_the_cards_once(both_sections, client, pi, project, paid_storage):
    client.force_login(pi)

    html = client.get(create_url(project), {"resource": paid_storage.pk}).content.decode()

    assert html.count('name="test_quota-quota"') == 1  # ColdFront's {{ form|crispy }} renders the container only
    assert html.count(f'id="{SECTIONS_CONTAINER_ID}"') == 1


def test_an_htmx_request_gets_just_the_cards(both_sections, client, pi, project, free_storage):
    client.force_login(pi)

    response = get_cards(client, project, free_storage)

    html = response.content.decode()
    assert response.status_code == 200
    assert html.strip().startswith(f'<div id="{SECTIONS_CONTAINER_ID}"')
    assert "<html" not in html
    assert 'data-request-section="test_quota"' in html and "test_billing" not in html


def test_another_htmx_request_gets_the_whole_page(both_sections, client, pi, project, free_storage):
    # e.g. hx-boost on a link to the page
    client.force_login(pi)

    response = client.get(create_url(project), {"resource": free_storage.pk}, headers={"HX-Request": "true"})

    assert "<html" in response.content.decode()


@pytest.mark.parametrize("cards", [False, True])
def test_the_page_and_the_cards_vary_on_the_htmx_headers(both_sections, client, pi, project, free_storage, cards):
    client.force_login(pi)

    if cards:
        response = get_cards(client, project, free_storage)
    else:
        response = client.get(create_url(project), {"resource": free_storage.pk})

    vary = {value.strip() for value in response.headers["Vary"].split(",")}
    assert {"HX-Request", "HX-Target"} <= vary


@pytest.mark.parametrize("cards", [False, True])
def test_cards_get_the_requests_context(both_sections, client, pi, project, free_storage, cards):
    client.force_login(pi)

    if cards:
        response = get_cards(client, project, free_storage)
    else:
        response = client.get(create_url(project), {"resource": free_storage.pk})

    # request.user, from the context processors
    assert '<span class="requested-by">asmith02</span>' in response.content.decode()


def test_a_normal_page_request_without_permission_is_still_refused(both_sections, client, project, free_storage):
    client.force_login(f.UserFactory(username="nobody01"))

    assert client.get(create_url(project), {"resource": free_storage.pk}).status_code == 403


def test_a_normal_page_request_is_still_redirected(both_sections, client, project, free_storage):
    response = client.get(create_url(project), {"resource": free_storage.pk})

    assert response.status_code == 302 and "HX-Refresh" not in response.headers


def test_a_request_is_complete_when_allocation_new_is_sent(both_sections, client, pi, project, paid_storage):
    seen = []

    def receiver(sender, allocation_pk, **kwargs):
        allocation = Allocation.objects.get(pk=allocation_pk)
        seen.append(allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE).exists())

    with receiver_connected(allocation_new, receiver, dispatch_uid="test-complete-request"):
        client.force_login(pi)
        response = client.post(create_url(project), request_data(project, paid_storage, **{"test_quota-quota": "10"}))

    assert response.status_code == 302
    assert seen == [True]


def test_an_invalid_request_creates_nothing_and_shows_the_cards_again(both_sections, client, pi, project, free_storage):
    sent = []

    with receiver_connected(
        allocation_new, lambda **kwargs: sent.append(kwargs), weak=False, dispatch_uid="test-invalid-request"
    ):
        client.force_login(pi)
        response = client.post(create_url(project), request_data(project, free_storage))

    html = response.content.decode()
    assert response.status_code == 200
    assert not Allocation.objects.exists() and sent == []
    assert SECTION_ERRORS_MESSAGE in html
    assert 'name="test_quota-quota"' in html


def test_the_summary_error_appears_once_on_the_page(both_sections, client, pi, project, free_storage):
    client.force_login(pi)

    html = client.post(create_url(project), request_data(project, free_storage)).content.decode()

    assert html.count(SECTION_ERRORS_MESSAGE) == 1


# -- Card requests the page refuses: the page reloads ---------------------------------------------


def test_the_cards_need_the_pages_permission(both_sections, client, project, free_storage):
    client.force_login(f.UserFactory(username="nobody01"))

    response = get_cards(client, project, free_storage)

    # refused: no cards, and a reload shows the page's refusal (htmx wouldn't swap a 403, leaving the
    # old resource's cards on the page)
    assert response.status_code == 200 and response.headers["HX-Refresh"] == "true"
    assert "data-request-section" not in response.content.decode()


def test_a_card_request_that_would_be_redirected_refreshes_the_page(both_sections, client, project, free_storage):
    # e.g. the session expired while the page was open: the login redirect mustn't land in the cards
    response = get_cards(client, project, free_storage)

    assert response.status_code == 200
    assert response.headers["HX-Refresh"] == "true"
    assert response.content == b""


def test_a_card_request_for_a_project_needing_review_refreshes_the_page(
    both_sections, client, pi, project, free_storage
):
    project.force_review = True
    project.save()
    client.force_login(pi)

    assert get_cards(client, project, free_storage).headers["HX-Refresh"] == "true"


def test_a_card_request_for_a_project_that_is_gone_refreshes_the_page(both_sections, client, pi, project, free_storage):
    client.force_login(pi)
    url = create_url(project)
    project.delete()

    response = client.get(url, {"resource": free_storage.pk}, headers=CARDS_REQUEST)

    assert response.headers["HX-Refresh"] == "true"


class ExplodingSection(QuotaSection):
    name = "test_exploding"

    def card_context(self):
        raise RuntimeError("pricing service down")


def test_a_card_request_that_fails_refreshes_the_page_and_logs_it(client, pi, project, free_storage, caplog):
    register_request_section(ExplodingSection)
    client.force_login(pi)

    with caplog.at_level(logging.ERROR, logger=VIEWS_LOGGER):
        response = get_cards(client, project, free_storage)

    assert response.headers["HX-Refresh"] == "true"
    assert "pricing service down" in caplog.text


def test_a_refreshed_card_request_doesnt_repeat_the_pages_message(both_sections, client, pi, project, free_storage):
    project.force_review = True
    project.save()
    client.force_login(pi)

    get_cards(client, project, free_storage)
    # the reload htmx's HX-Refresh makes
    reload = client.get(create_url(project), {"resource": free_storage.pk})

    shown = [str(message) for message in get_messages(reload.wsgi_request)]
    assert len(shown) == 1 and "review your project" in shown[0]


def test_a_refreshed_card_request_keeps_messages_from_earlier_requests(
    both_sections, client, pi, project, paid_storage, free_storage
):
    other = managed_project(pi)
    client.force_login(pi)
    # queues "Allocation requested..." for the next page, which the user hasn't seen yet
    client.post(create_url(other), request_data(other, paid_storage, **{"test_quota-quota": "10"}))
    project.force_review = True
    project.save()

    get_cards(client, project, free_storage)
    reload = client.get(create_url(project), {"resource": free_storage.pk})

    shown = [str(message) for message in get_messages(reload.wsgi_request)]
    assert any("Allocation requested" in message for message in shown)
    assert sum("review your project" in message for message in shown) == 1


# -- A page that doesn't show the cards ---------------------------------------------------------


def test_a_failed_submit_on_a_page_without_the_cards_is_logged(
    both_sections, page_without_cards, client, pi, project, free_storage, caplog
):
    client.force_login(pi)

    with caplog.at_level(logging.ERROR, logger=VIEWS_LOGGER):
        response = client.post(create_url(project), request_data(project, free_storage))

    assert response.status_code == 200 and not Allocation.objects.exists()
    assert "doesn't render form.request_sections" in caplog.text


def test_the_log_names_only_the_cards_with_errors(
    both_sections, page_without_cards, client, pi, project, paid_storage, caplog
):
    client.force_login(pi)
    data = request_data(project, paid_storage, **{"test_quota-quota": "10", "test_billing-grant": "NIH R01"})

    with caplog.at_level(logging.ERROR, logger=VIEWS_LOGGER):
        client.post(create_url(project), data)

    assert "test_billing" in caplog.text and "test_quota" not in caplog.text


def test_a_failed_submit_on_a_page_with_the_cards_isnt_logged(both_sections, client, pi, project, free_storage, caplog):
    client.force_login(pi)

    with caplog.at_level(logging.ERROR, logger=VIEWS_LOGGER):
        client.post(create_url(project), request_data(project, free_storage))

    assert "request_sections" not in caplog.text


def test_a_request_page_without_the_cards_gets_a_warning(both_sections, page_without_cards):
    [warning] = checks.check_request_page(None)

    assert isinstance(warning, django_checks.Warning) and warning.id == check_id("W002")


def test_coldfronts_own_request_page_renders_the_cards(both_sections):
    assert checks.check_request_page(None) == []


def test_a_page_without_the_cards_is_fine_while_no_section_is_registered(page_without_cards):
    assert checks.check_request_page(None) == []


@pytest.mark.parametrize(
    "page", ["whole_form", "as_div", "field_loop", "with_include", "visible_fields", "multiline_hash"]
)
def test_pages_that_render_the_whole_form_or_include_others_get_no_warning(both_sections, monkeypatch, page):
    monkeypatch.setattr(AllocationRequestCreateView, "template_name", f"test_pages/{page}.html")

    assert checks.check_request_page(None) == []


# -- The view and the URL -------------------------------------------------------------------------


def test_the_app_puts_its_view_on_coldfronts_request_url():
    assert allocation_views.AllocationCreateView is AllocationRequestCreateView
    assert issubclass(AllocationRequestCreateView, coldfront_create_view())
    assert coldfront_create_view().__module__ == "coldfront.core.allocation.views"


def test_the_request_url_uses_this_apps_view():
    assert checks.request_page_view_class() is AllocationRequestCreateView


def test_the_view_left_in_place_passes():
    assert checks.check_create_view(None) == []


def test_installing_again_is_harmless():
    install_create_view()

    assert allocation_views.AllocationCreateView is AllocationRequestCreateView


def test_installing_over_another_apps_view_is_refused(monkeypatch):
    monkeypatch.setattr(
        allocation_views, "AllocationCreateView", type("BillingCreateView", (coldfront_create_view(),), {})
    )

    with pytest.raises(ImproperlyConfigured, match="BillingCreateView"):
        install_create_view()


def test_a_view_that_isnt_coldfronts_is_refused_with_a_clear_error(monkeypatch):
    monkeypatch.setattr(allocation_views, "AllocationCreateView", type("UnrelatedView", (), {}))

    with pytest.raises(ImproperlyConfigured, match="UnrelatedView"):
        coldfront_create_view()


def test_another_app_replacing_the_view_afterwards_is_an_error(monkeypatch):
    monkeypatch.setattr(
        allocation_views, "AllocationCreateView", type("StorageCreateView", (coldfront_create_view(),), {})
    )

    [error] = checks.check_create_view(None)

    assert error.id == check_id("E002")


def test_a_request_url_that_kept_another_view_is_an_error(monkeypatch):
    monkeypatch.setattr(checks, "request_page_view_class", lambda: coldfront_create_view())

    [error] = checks.check_create_view(None)

    assert error.id == check_id("E002")


@pytest.mark.parametrize("problem", [NoReverseMatch("no allocation-create"), AttributeError("no view_class")])
def test_a_request_url_that_cant_be_checked_is_an_error_not_a_crash(monkeypatch, problem):
    def unresolvable():
        raise problem

    monkeypatch.setattr(checks, "request_page_view_class", unresolvable)

    [error] = checks.check_create_view(None)

    assert error.id == check_id("E002")
    assert str(problem) in error.msg


# -- Signals and the sender check -----------------------------------------------------------------


def test_sent_by_takes_the_view_and_its_subclasses():
    assert sent_by(AllocationRequestCreateView, coldfront_create_view())
    assert sent_by(coldfront_create_view(), coldfront_create_view())
    assert not sent_by(coldfront_create_view(), AllocationRequestCreateView)


@pytest.mark.parametrize("sender", [None, "allocation_create", object()])
def test_sent_by_is_false_for_senders_that_arent_classes(sender):
    # Allocation.add_user sends allocation_activate_user with sender=None by default
    assert not sent_by(sender, coldfront_create_view())


@pytest.mark.parametrize("signal", [allocation_new, allocation_activate_user])
def test_a_receiver_filtering_on_coldfronts_view_is_an_error(signal):
    # ColdFront sends the request page's signals with this app's view as sender
    with receiver_connected(
        signal, receiver_that_does_nothing, sender=coldfront_create_view(), dispatch_uid="test-sender-filter"
    ):
        errors = checks.check_sender_filters(None)

    [error] = errors
    assert isinstance(error, django_checks.Error) and error.id == check_id("E004")
    assert "coldfront_utils.signals.sent_by" in error.hint


def test_receivers_not_filtering_on_the_view_pass():
    with receiver_connected(allocation_new, receiver_that_does_nothing, dispatch_uid="test-no-sender-filter"):
        assert checks.check_sender_filters(None) == []


def test_the_sender_check_still_runs_after_an_unrelated_view_replaces_coldfronts(monkeypatch):
    # check_create_view reports the replacement (E002); this check mustn't crash on it instead
    monkeypatch.setattr(allocation_views, "AllocationCreateView", type("UnrelatedView", (), {}))

    assert checks.check_sender_filters(None) == []


# -- Other system checks --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "error_code", "named"),
    [
        (["test_quota", "test_typo"], "E001", "test_typo"),  # not registered
        (["test_quota", "test_quota"], "E003", "test_quota"),  # listed twice
    ],
)
def test_a_setting_naming_the_wrong_sections_is_an_error(both_sections, settings, setting, error_code, named):
    settings.ALLOCATION_REQUEST_SECTIONS = setting

    [error] = checks.check_section_setting(None)

    assert isinstance(error, django_checks.Error) and error.id == check_id(error_code)
    assert named in error.msg


@pytest.mark.parametrize("setting", [["test_quota"], None])
def test_a_setting_naming_registered_sections_or_none_passes(both_sections, settings, setting):
    settings.ALLOCATION_REQUEST_SECTIONS = setting

    assert checks.check_section_setting(None) + checks.check_sections(None) + checks.check_request_page(None) == []


@pytest.mark.parametrize("setting", ["test_quota", 5, {"test_quota": True}, "", 0])
def test_a_setting_that_isnt_a_list_is_reported_not_a_crash(both_sections, settings, setting):
    settings.ALLOCATION_REQUEST_SECTIONS = setting

    every_check = checks.check_section_setting(None) + checks.check_sections(None) + checks.check_request_page(None)

    assert [message.id for message in every_check] == [check_id("E006")]


class TemplatelessSection(QuotaSection):
    name = "test_templateless"
    template = ""


class MisspelledTemplateSection(QuotaSection):
    name = "test_misspelled"
    template = "test_sections/quoat.html"


class FormlessSection(QuotaSection):
    name = "test_formless"
    form_class = forms.Form  # not a RequestSectionForm: it can't take the request


@pytest.mark.parametrize("section_class", [TemplatelessSection, MisspelledTemplateSection, FormlessSection])
def test_a_section_that_cant_render_or_build_its_form_is_an_error(section_class):
    register_request_section(section_class)

    [error] = checks.check_sections(None)

    assert error.id == check_id("E005")
    assert section_class.name in error.msg


def test_well_made_sections_pass(both_sections):
    assert checks.check_sections(None) == []


# -- What the app needs from ColdFront ------------------------------------------------------------


def test_the_installed_coldfront_has_what_the_app_needs():
    apps.require_coldfront_features()


def test_a_coldfront_whose_request_form_isnt_a_model_form_is_refused(monkeypatch):
    # ColdFront before 1.1.8
    monkeypatch.setattr(allocation_forms, "AllocationForm", type("AllocationForm", (forms.Form,), {}))

    with pytest.raises(ImproperlyConfigured, match="1.1.8"):
        apps.require_coldfront_features()


def test_a_coldfront_without_django_htmx_is_refused(monkeypatch):
    monkeypatch.setattr(apps, "find_spec", lambda name: None)

    with pytest.raises(ImproperlyConfigured, match="django-htmx"):
        apps.require_coldfront_features()


def test_the_check_doesnt_need_coldfronts_package_metadata(monkeypatch):
    # ColdFront run from a source checkout has none
    def no_metadata(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", no_metadata)

    apps.require_coldfront_features()
