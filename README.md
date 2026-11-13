# Coldfront Common Utilities

utilities and helper functions for coldfront and coldfront plugins

## Optional extras

- `ad`: needed for `coldfront_utils.util.ad_search`. It installs coldfront's `ldap` extra, which
  includes python-ldap; python-ldap compiles against the OpenLDAP client libraries.

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

## One allocation request form: `coldfront_utils.allocation_request`

Plugins add what they need from a new allocation request, such as a storage quota or a cost center,
to ColdFront's own request form, as cards. The request is complete when it's submitted, so ColdFront's
admin email, `allocation_new` and everything listening to it see the plugins' parts too. There's no
second page, and no half-finished request if someone gives up. `docs/allocation-request-plan.md`
has the design.

Install the app (it needs ColdFront 1.1.8 or later, and refuses to start on anything older):

```python
INSTALLED_APPS += ["coldfront_utils.allocation_request"]
```

Before installing it, check two things on the site, or startup or requests fail:

- **No plugin replaces `AllocationCreateView` any more.** Plugins that used to (storage, billing) must
  first move to request sections, and their receivers to `sent_by`.
- **The request page renders the cards.** ColdFront's own page does. A site's own
  `allocation/allocation_create.html` must contain `{{ form.request_sections }}` before any section with
  a required field is registered, or requests needing that card can't be submitted.

It makes ColdFront's request page (`AllocationCreateView`) use `AllocationRequestForm`, by replacing
the view with its own subclass in `ready()`. So no plugin may replace that view itself:

- a plugin that replaced it earlier in `INSTALLED_APPS` stops startup with `ImproperlyConfigured`,
  naming the plugin's view, rather than being silently dropped;
- one that replaces it later, or a URLconf imported before the app's `ready()` that kept another
  view, is the system check error `coldfront_utils.allocation_request.E002`.

**Signal senders.** ColdFront's request page sends `allocation_new` and `allocation_activate_user`
with `sender=self.__class__`, which is now this app's view. A receiver connected with
`sender=AllocationCreateView` filters on whichever class that name held when its module was imported,
so it fires or not depending on `INSTALLED_APPS` order. Take any sender and check it with
`coldfront_utils.signals.sent_by(sender, AllocationCreateView)` instead: unlike a bare `issubclass`,
it copes with senders that aren't classes, such as the `None` that `Allocation.add_user` sends by
default. A receiver filtering on ColdFront's own view never runs once this app is installed, so it's
the system check error `coldfront_utils.allocation_request.E004`.

**The page.** ColdFront's own request page renders the whole form, so the cards appear after its
fields. A site whose `allocation/allocation_create.html` renders fields one by one adds
`{{ form.request_sections }}` where the cards go. Changing the resource fetches that resource's cards
with htmx, from the request page's own URL, so they pass the page's permission checks. The view
answers with just the cards only when htmx asks for them for their container (`HX-Target`
`allocation-request-sections`); any other request, htmx's `hx-boost` included, gets the page. When the
page would refuse instead (an expired session, a project that needs review or is gone, a user no
longer allowed), or the cards fail to render (logged), a request for the cards gets htmx's `HX-Refresh`, so the browser reloads the page and shows
the refusal properly; the refusal's message, which the reload queues again, isn't left behind twice. Both
responses vary on `HX-Request` and `HX-Target`, so a cache doesn't serve one for the other.

A page that forgets `{{ form.request_sections }}` can't complete a request that needs a card, and
wouldn't say why. The system check `coldfront_utils.allocation_request.W002` warns when sections are
registered and the request page's template doesn't appear to render them, and the view logs an error
when a request is refused for its cards on a page that didn't show them.

**A section** is a plugin's card. Subclass `RequestSection`, give it a form (a `RequestSectionForm`
subclass) and a template, and register it in the plugin's `AppConfig.ready()`:

```python
from coldfront_utils.allocation_request import RequestSection, RequestSectionForm, register_request_section


class QuotaForm(RequestSectionForm):
    quota = forms.DecimalField(label="Storage Quota (TB)", min_value=Decimal("0.1"))


@register_request_section
class QuotaSection(RequestSection):
    name = "storage_quota"  # also the form's prefix: lowercase letters, digits, underscores
    order = 10
    form_class = QuotaForm
    template = "storage/allocation_request/quota.html"

    def applies(self):
        return self.resource.resource_type.name == "Storage"

    def save(self, allocation, form):
        # e.g. create the Storage Quota (TB) allocation attribute
        pass
```

- **Each request gets its own section.** The form builds the section with the request's details:
  `self.resource` (the chosen one), `self.project`, `self.user` (who is requesting) and
  `self.request` (None outside a request). Its methods read them from `self`, and may keep what they
  look up there for the rest of the request, e.g. an account found in `applies()` and used in `save()`.

- **Which cards show.** A section's card shows when `applies()` is true for the chosen resource. Its
  form is built with the project, the user and the resource (`self.project`, `self.user`,
  `self.resource`), so its `clean()` can check fields against them.
- **Validation.** On submit, the form validates ColdFront's fields and the forms of the sections that
  apply to the *posted* resource, even when ColdFront's own checks fail. If one is invalid, nothing is
  saved, and the page comes back with each card's errors at its fields and "Please correct the errors
  below." above the cards.
- **Saving.** `save()` runs after the allocation is saved and before ColdFront attaches the resource,
  inside a transaction with the allocation, so a section that fails rolls the allocation back. It
  shows the user an error page, though, so check anything that can go wrong in the form, or with a
  system check. The transaction covers the allocation and the sections only: ColdFront's view
  attaches the resource, adds the PI and users and sends `allocation_new` after it has committed, as
  ColdFront does without this app (`ATOMIC_REQUESTS` aside). So don't queue tasks from `save()`, where
  they could run before the request is finished or after a later section rolled it back. Work that
  needs the finished request belongs in an `allocation_new` receiver, checking its sender with
  `sent_by`.
- **The project** is the one in the page's URL, the one its permission check and the sections ran
  for: the form ignores a posted `project`.
- **Uploads.** A section form gets the request's uploaded files, but the page's `<form>` needs
  `enctype="multipart/form-data"` to send them; ColdFront's own request page doesn't have it.
- **The card's template** extends `allocation_request/card.html` and fills its blocks, `heading`,
  `intro` and `fields` (by default, the form as crispy renders it). Its context is `section`,
  `section_form`, `resource`, `project`, and whatever the section's `card_context()`
  returns, plus what the site's context processors add (`request`, `user`, `settings`), as on the
  page. The intro sits in a `<div>`, so it can hold paragraphs or a list; a card with no intro overrides
  `intro_wrapper` with nothing. Put the intro text in a small template of its own, included from `intro`, so a site can
  change the wording by overriding just that file. Scripts in a card start on page load and on
  htmx's `htmx:load` event: ColdFront's bundle doesn't make `htmx` a global.
- **Which sections, in what order.** By default every registered section, by `order`. The setting
  `ALLOCATION_REQUEST_SECTIONS = ["storage_quota", ...]` chooses them and their order instead; a name
  nothing registered is the system check error `coldfront_utils.allocation_request.E001`, a name
  listed twice is `E003` (the section is used once), and a setting that isn't a list is `E006`.
- **A section that can't be shown,** whose `form_class` isn't a `RequestSectionForm` or whose template
  is missing, is `E005`: every request for its resources would otherwise fail.
- **`save(commit=False)`** works as Django's forms do: the caller saves the allocation, then calls
  `save_m2m()`, which saves the sections too.
- **When the checks run.** Django runs system checks with management commands (`check`, `migrate`,
  `runserver`), not when gunicorn or uWSGI start the app, so a deploy that runs `migrate`, as Tufts'
  playbook does, is what stops on an error. The `ImproperlyConfigured` errors stop any start.

## Checking attribute types named in settings: `coldfront_utils.attribute.checks`

Plugins look attribute types up by a name from their settings (`QUOTA_ATTRIBUTE_NAME`, ...). A
renamed setting, a deleted type or a hand-made duplicate then fails quietly, or as a server error when
someone uses the page. `attribute_type_warnings()` reports them as system check warnings, one for each
name that no type has, or more than one does. It takes any of ColdFront's attribute type models
(`AllocationAttributeType`, `ResourceAttributeType`, `ProjectAttributeType`):

```python
from django.core import checks

from coldfront.core.allocation.models import AllocationAttributeType

from coldfront_utils.attribute.checks import attribute_type_warnings


@checks.register(checks.Tags.database)
def check_quota_type(app_configs, databases=None, **kwargs):
    return attribute_type_warnings(
        AllocationAttributeType,
        [QUOTA_ATTRIBUTE_NAME],
        databases=databases,
        check_id="storage.W002",
        consequence="requests that ask for a quota are refused",
    )
```

Register it with `Tags.database`: Django then runs it from `migrate` and `check --database`, not on
every start, and passes `databases`. Without `databases` it queries nothing, and while the tables
can't be read yet it reports nothing, so it can't stop the `migrate` that creates the types. Report
warnings, not errors, for the same reason: `migrate` runs the checks before it migrates.

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
