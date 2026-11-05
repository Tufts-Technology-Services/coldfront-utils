"""Who is acting, in a web request and in the background tasks it leads to.

Add "coldfront_utils.task_context" to INSTALLED_APPS, and
"coldfront_utils.task_context.middleware.ActingUserMiddleware" to MIDDLEWARE after
AuthenticationMiddleware. Then:

- get_acting_username() gives the request's user in the web process;
- each task queued while someone is acting carries them in its package, under "acting_user";
- while the task runs, get_acting_username() gives them there too, simple_history records them as
  the user of every history row the task writes, and tasks it queues carry them on;
- a task whose result is a dict names them in it, so the task report shows who queued it.

A chain's later links and scheduled runs, @reschedulable retries among them, are queued by the
cluster itself, where nobody is acting, so they carry no user.
"""

from .context import acting_as, get_acting_username

__all__ = ["acting_as", "get_acting_username"]
