from .context import acting_as
from .su import signed_in_by


class ActingUserMiddleware:
    """Make the request's user the acting user while the request is handled.

    So code with no request to hand, such as a signal receiver, can find who is acting with
    get_acting_username(), and a task queued during the request carries them. Put it after
    AuthenticationMiddleware, which sets request.user.

    Where an administrator used django-su to sign in as the request's user, the administrator is the
    acting user, because they are the one acting, and get_impersonated_username() names the account
    they are in. The administrator is also on request.signed_in_by (None for anyone else), for a
    template that says who is really working.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.signed_in_by = None
        if not request.user.is_authenticated:
            return self.get_response(request)
        # get_username(), not .username: a custom user model names its login field itself. Once
        # the request is handled, acting_as puts back whoever acted before, so the user can't leak
        # into the next request this thread handles.
        administrator = signed_in_by(request)
        if administrator is None or administrator.pk == request.user.pk:
            with acting_as(request.user.get_username()):
                return self.get_response(request)
        request.signed_in_by = administrator
        with acting_as(administrator.get_username(), impersonating=request.user.get_username()):
            return self.get_response(request)
