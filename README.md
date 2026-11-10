# Coldfront Common Utilities

utilities and helper functions for coldfront and coldfront plugins

## Optional extras

- `ad`: needed for `coldfront_utils.util.ad_search`. It installs coldfront's `ldap` extra, which
  includes python-ldap; python-ldap compiles against the OpenLDAP client libraries.
- `ssh`: fabric.

```
coldfront-utils[ad] @ git+https://github.com/Tufts-Technology-Services/coldfront-utils.git
```

## Tasks that wait: `coldfront_utils.tasks`

`@reschedulable` is for django-q tasks that wait minutes for something outside ColdFront, such as
a new group reaching Active Directory or a new share mounting on a cluster. When the task can't
finish yet, it schedules itself to run again later with the same arguments, instead of failing.

```python
from coldfront_utils.tasks import RetryLater, reschedulable


@reschedulable(retry_on=(GroupNotInADError,), retries=5, wait=5)
def create_lab_share(native_path, owner, group, allocation_pk, share_provisioned=False):
    if not share_provisioned:
        provision_share(native_path, owner, group)
    if not cluster_directory_visible(native_path):
        # run again later, and skip provisioning next time
        raise RetryLater(f"{native_path} isn't visible on the cluster yet", share_provisioned=True)
    create_folders(native_path, owner, group)
```

- **When it retries:** the body raises one of the `retry_on` exception types, or `RetryLater`. Any
  other exception propagates as usual.
- **What it schedules:** a one-off django-q schedule of the same task, `wait` minutes later, with
  the call's arguments by keyword. A `RetryLater` can change some of those arguments, to carry
  progress into the next run. Pass `q_options` (a dict, or a function of the task's arguments) to
  give the retry a task name or group.
- **What the run returns:** a `Rescheduled` result, so django-q records the run as successful. It's
  a dict whose `"message"` is `"scheduled:<reason> (runs again in 5 minutes, 4 retries left)"`. A
  decorator around the task can check `isinstance(result, Rescheduled)`.
- **When retries run out:** the error that would have caused the next retry is raised, so the task
  fails with its own reason. A `RetryLater` raised from another exception (`raise RetryLater(...)
  from error`) gives way to that exception. A call can pass `retries=` and `wait=` to override the
  decorator's.

The task has to be callable the way its retry calls it, and the decorator checks this when the
module loads:

- it's defined at module level, so the retry can import it by path;
- it takes every argument by keyword: no positional-only parameters or `*args`;
- no parameter uses a name the retry needs (`retries`, `wait`, `_acting_user`) or that django-q
  takes for itself (`name`, `timeout`, `task_name` and others; see `DJANGO_Q_ARGUMENTS`).

Every argument also has to be a literal: a string, number, boolean, `None`, or a list, tuple, set
or dict of those. django-q stores a schedule's arguments as text and reads them back with
`ast.literal_eval`. If that fails, the retry runs with no arguments at all. So the decorator refuses
anything else, such as a `Path`, before scheduling.

`build_task_result(state, reason)` builds the `{"message": "<state>:<reason>"}` result the
decorator and the storage plugin's tasks return, so the task report reads them all the same way.

Why not django-q2's own retry? It redelivers a failed task, but only on brokers that track
acknowledgement, which Redis doesn't. It also uses one delay for the whole cluster, retries every
failure, and can't change the arguments.

## Who is acting: `coldfront_utils.task_context`

Who did something, in the web request and in every background task it leads to. Install it with
two settings:

```python
INSTALLED_APPS += ["coldfront_utils.task_context"]
# after django.contrib.auth.middleware.AuthenticationMiddleware
MIDDLEWARE += ["coldfront_utils.task_context.middleware.ActingUserMiddleware"]
```

- **In a web request,** `get_acting_username()` gives the request's user. `acting_as(username)` sets
  it for a block, for management commands and tests.
- **When an administrator used django-su to sign in as someone,** the administrator is the acting
  user, because they are the one acting, however many accounts they have signed in as since:
  `get_impersonated_username()` names the account they are in, and is `None` otherwise.
  simple_history records the administrator as the user of every row they write, with the change
  reason `signed in as <username>` (added to any reason the code gave, within the column's length).
  A user the code sets on the instance itself (`_history_user`) is left alone. Tasks they queue
  carry the administrator. `acting_as(username, impersonating=...)` does the same for a block.
  The middleware also sets `request.signed_in_by` to the administrator (`None` for anyone else),
  for a template that says who is really working, such as django-su's banner.
- **A task queued while someone is acting** carries them in its package, under `"acting_user"`.
  That covers every `async_task`, whichever plugin or ColdFront itself queues it, without changing
  any task.
- **While the task runs,** `get_acting_username()` gives the same user, and tasks it queues carry
  them on. simple_history records them as the user of every history row the task writes: without
  this, a worker has no request, so those rows have no user.
- **A task whose result is a dict** names them in it under `"acting_user"`, so the task report
  shows who queued it. The `Task` table has no column for it.

- **Tasks the cluster queues itself** are queued with nobody acting: a chain's later links, by the
  monitor, and scheduled runs, by the scheduler. So they're told who to act for, with the reserved
  keyword `_acting_user` (`ACTING_USER_ARGUMENT`). The worker takes it out of the task's arguments
  just before calling the function, so the function never sees it, and `None` means nobody:
  - a chain gives each of its links the user it was started with;
  - `@reschedulable` gives each retry the user its run acted for;
  - any other `schedule()` can pass `_acting_user=get_acting_username()` itself. A periodic
    schedule made in the admin has nobody to act for, and runs with no user.

  The name is reserved, as django-q reserves `hook`, `group` and `timeout`: a task's own
  `_acting_user` parameter would never receive its argument. `@reschedulable` refuses a task that
  has one.

  The workers have to run this app whenever a queued task can carry the keyword. Deploy it to the
  cluster no later than to the web app. Before removing it from `INSTALLED_APPS`, let queued
  retries and chains finish: an older worker would pass the keyword to the function, which fails
  with an unexpected keyword argument.

The helpers `from coldfront_utils import ...` gives are imported on first use, not when the package
is, so that this app can be installed: Django imports it while it's loading apps, before ColdFront's
models can be imported.

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

The tests run against coldfront's own settings (`tests/settings.py`) with an in-memory SQLite
database. They live in `tests/` rather than inside the package because several of the helpers
import coldfront's models, which can't happen before Django's settings load.
