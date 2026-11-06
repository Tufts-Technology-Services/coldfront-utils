from .context import acting_as


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
        # get_username(), not .username: a custom user model names its login field itself. Once
        # the request is handled, acting_as puts back whoever acted before, so the user can't leak
        # into the next request this thread handles.
        with acting_as(request.user.get_username()):
            return self.get_response(request)
