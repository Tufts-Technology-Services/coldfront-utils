"""django-su: who signed in as whom."""

from django.contrib.auth import get_user_model

# where django-su keeps the users a session was signed in as before: a list of (user pk, backend),
# oldest first, so the first is who really signed in
SU_SESSION_KEY = "exit_users_pk"


def signed_in_by(request):
    """The user who used django-su to sign in as the request's user, or None if nobody did.

    That is the first user django-su remembers, whoever they have since signed in as: an
    administrator who signed in as one user and then another is still the administrator. None too
    when that user no longer exists, or the request has no session.
    """
    session = getattr(request, "session", None)
    remembered = session.get(SU_SESSION_KEY) if session is not None else None
    if not remembered:
        return None
    try:
        first_pk = remembered[0][0]
        return get_user_model()._default_manager.filter(pk=first_pk).first()
    except (IndexError, TypeError, ValueError):
        # a session this doesn't recognise is treated as one django-su never touched
        return None
