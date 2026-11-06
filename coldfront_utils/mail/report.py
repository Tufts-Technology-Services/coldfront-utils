import datetime
import json
import logging
from decimal import Decimal

from django.core.mail import EmailMessage

from coldfront.core.utils.common import import_from_settings

logger = logging.getLogger(__name__)


def mail_report(title, filename, content, mimetype, report_date, body, summary):
    """Send a report to REPORTING_EMAIL as an attachment.

    `title` is the subject line (the date is appended), `mimetype` says what the attachment
    is ("text/csv", "application/json", ...) and `summary` is a short description of what
    the report holds, used to build the return value. That is a small dict rather than the
    report itself, since a task queue such as django-q stores it as the task result.
    """
    # its own switch rather than coldfront's EMAIL_ENABLED: scheduled reports to staff and
    # transactional mail to users are worth turning on and off independently
    if not import_from_settings("REPORTING_EMAIL_ENABLED", False):
        logger.warning(f"REPORTING_EMAIL_ENABLED is off: built the {title.lower()} for {report_date} but sent nothing.")
        return {"message": f"{summary}; not sent, reporting email is disabled"}

    # no default: a deployment that hasn't named a recipient should raise an error
    recipient = import_from_settings("REPORTING_EMAIL")
    subject_prefix = import_from_settings("EMAIL_SUBJECT_PREFIX", "")
    message = EmailMessage(
        subject=f"{subject_prefix} {title} {report_date}".strip(),
        body=body,
        # EmailMessage falls back to DEFAULT_FROM_EMAIL when this is empty, which is what
        # coldfront leaves EMAIL_SENDER as until a deployment sets it
        from_email=import_from_settings("EMAIL_SENDER", ""),
        to=[recipient],
    )
    message.attach(filename, content, mimetype)
    message.send(fail_silently=False)
    logger.info(f"Sent the {title.lower()} for {report_date} to {recipient}.")
    return {"message": f"{summary} sent to {recipient}"}


def json_default(value):
    """`default` for json.dumps: Decimals become floats, anything else unserializable its str()."""
    if isinstance(value, Decimal):
        return float(value)
    return str(value)  # catches User objects, datetimes, etc.


def mail_json_report(title, filename_stem, rows):
    """Serialize `rows` and mail them to REPORTING_EMAIL as a JSON attachment dated today."""
    report_date = datetime.date.today().isoformat()
    return mail_report(
        title=title,
        filename=f"{filename_stem}_{report_date}.json",
        content=json.dumps(rows, indent=4, default=json_default),
        mimetype="application/json",
        report_date=report_date,
        body=f"{title} for {report_date}, attached as JSON.",
        summary=f"{len(rows)} rows",
    )
