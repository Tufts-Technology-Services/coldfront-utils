import datetime
import json
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured

from coldfront_utils import mail_json_report, mail_report

RECIPIENT = "data.engineer@tufts.edu"


@pytest.fixture
def mail(settings, mailoutbox):
    """A deployment configured to send, and the outbox to read the result out of.

    Nothing defines REPORTING_EMAIL_ENABLED and coldfront leaves EMAIL_SENDER empty, so
    without this mail_report takes its "not configured" path instead.
    """
    settings.REPORTING_EMAIL_ENABLED = True
    settings.EMAIL_SENDER = "coldfront@tufts.edu"
    settings.EMAIL_SUBJECT_PREFIX = "[ColdFront]"
    settings.REPORTING_EMAIL = RECIPIENT
    return mailoutbox


def send_json_report(**overrides):
    arguments = {
        "title": "Allotments report",
        "filename": "allotments_2026-11-02.json",
        "content": json.dumps([{"user": "smith", "amount": 5}]),
        "mimetype": "application/json",
        "report_date": "2026-11-02",
        "body": "Allotments report for 2026-11-02, attached as JSON.",
        "summary": "1 rows",
        **overrides,
    }
    return mail_report(**arguments)


def test_sends_one_message_to_the_reporting_address(mail):
    send_json_report()

    assert len(mail) == 1
    assert mail[0].to == [RECIPIENT]
    assert mail[0].from_email == "coldfront@tufts.edu"
    assert mail[0].subject == "[ColdFront] Allotments report 2026-11-02"
    assert mail[0].body == "Allotments report for 2026-11-02, attached as JSON."


def test_attaches_the_content_under_the_given_name_and_type(mail):
    send_json_report()

    filename, content, mimetype = mail[0].attachments[0]
    assert filename == "allotments_2026-11-02.json"
    assert mimetype == "application/json"
    assert json.loads(content) == [{"user": "smith", "amount": 5}]


def test_a_csv_attachment_keeps_its_text(mail):
    send_json_report(filename="quotas.csv", content="mountpoint,quota\n/tier2/lab,1000\n", mimetype="text/csv")

    filename, content, mimetype = mail[0].attachments[0]
    assert (filename, mimetype) == ("quotas.csv", "text/csv")
    assert content == "mountpoint,quota\n/tier2/lab,1000\n"


def test_an_empty_subject_prefix_leaves_no_leading_space(mail, settings):
    settings.EMAIL_SUBJECT_PREFIX = ""

    send_json_report()

    assert mail[0].subject == "Allotments report 2026-11-02"


def test_falls_back_to_the_django_sender_when_coldfront_has_none(mail, settings):
    """An empty EMAIL_SENDER would otherwise go out as a literal empty From header."""
    settings.EMAIL_SENDER = ""
    settings.DEFAULT_FROM_EMAIL = "webmaster@tufts.edu"

    send_json_report()

    assert mail[0].from_email == "webmaster@tufts.edu"


def test_returns_a_summary_rather_than_the_report(mail):
    """A task queue stores the return value, so it should say what happened without carrying
    the whole attachment into the database."""
    assert send_json_report() == {"message": f"1 rows sent to {RECIPIENT}"}


def test_sends_nothing_when_reporting_email_is_disabled(mail, settings):
    settings.REPORTING_EMAIL_ENABLED = False

    result = send_json_report()

    assert mail == []
    assert result == {"message": "1 rows; not sent, reporting email is disabled"}


def test_the_switch_is_off_until_a_deployment_sets_it(settings, mailoutbox):
    """Nothing defines REPORTING_EMAIL_ENABLED, so the absent setting has to read as off."""
    settings.REPORTING_EMAIL = RECIPIENT

    send_json_report()

    assert mailoutbox == []


def test_coldfronts_own_mail_switch_does_not_gate_the_report(mail, settings):
    """A deployment can keep transactional mail to users off while still sending its own staff
    the scheduled reports."""
    settings.EMAIL_ENABLED = False

    send_json_report()

    assert len(mail) == 1


def test_an_unconfigured_recipient_is_an_error(settings, mailoutbox):
    """Better to fail the task visibly than to let the report go missing every run."""
    settings.REPORTING_EMAIL_ENABLED = True

    with pytest.raises(ImproperlyConfigured, match="REPORTING_EMAIL"):
        send_json_report()

    assert mailoutbox == []


def test_json_report_is_named_and_dated_today(mail):
    today = datetime.date.today().isoformat()

    mail_json_report("Allotments report", "allotments", [{"user": "smith"}])

    filename, content, mimetype = mail[0].attachments[0]
    assert filename == f"allotments_{today}.json"
    assert mimetype == "application/json"
    assert json.loads(content) == [{"user": "smith"}]
    assert mail[0].subject == f"[ColdFront] Allotments report {today}"


def test_json_report_summary_counts_the_rows(mail):
    result = mail_json_report("Allotments report", "allotments", [{"user": "smith"}, {"user": "jones"}])

    assert result == {"message": f"2 rows sent to {RECIPIENT}"}


def test_json_report_writes_decimals_as_numbers_and_other_objects_as_text(mail):
    """Billing amounts come out of the database as Decimals, which json.dumps can't write."""
    when = datetime.date(2026, 11, 2)

    mail_json_report("Cost report", "cost", [{"cost": Decimal("12.50"), "on": when}])

    _filename, content, _mimetype = mail[0].attachments[0]
    assert json.loads(content) == [{"cost": 12.5, "on": "2026-11-02"}]
