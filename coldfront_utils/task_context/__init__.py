"""Who is acting, in a web request and in the background tasks it leads to.

Add "coldfront_utils.task_context" to INSTALLED_APPS, and
"coldfront_utils.task_context.middleware.ActingUserMiddleware" to MIDDLEWARE after
AuthenticationMiddleware. Then:

- get_acting_username() gives the request's user in the web process;
- each task queued while someone is acting carries them in its package, under "acting_user";
- while the task runs, get_acting_username() gives them there too, simple_history records them as
  the user of every history row the task writes, and tasks it queues carry them on;
- a task whose result is a dict names them in it, so the task report shows who queued it.

A chain's later links and scheduled runs are queued by the cluster itself, where nobody is acting.
A chain carries its user to its later links, and @reschedulable to its retries. Any other
schedule() can pass its task the keyword ACTING_USER_ARGUMENT, `_acting_user=...`, which the
worker takes out of the arguments just before calling the task, so the task function never sees
it.
"""

from .context import ACTING_USER_ARGUMENT, acting_as, get_acting_username

__all__ = ["ACTING_USER_ARGUMENT", "acting_as", "get_acting_username"]
