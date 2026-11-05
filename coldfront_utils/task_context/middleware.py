from .context import reset_acting_username, set_acting_username


class ActingUserMiddleware:
    """Make the request's user the acting user while the request is handled.

    So code with no request to hand, such as a signal receiver, can find who is acting with
    get_acting_username(), and a task queued during the request carries them. Put it after
    AuthenticationMiddleware, which sets request.user.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)
        token = set_acting_username(request.user.username)
        try:
            return self.get_response(request)
        finally:
            # so the user can't leak into whatever this thread or task handles next
            reset_acting_username(token)
