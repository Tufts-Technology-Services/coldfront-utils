# Plan: one allocation request form that plugins add to

Status: in progress, 2026-11-12. Step 1 (the `coldfront_utils.allocation_request` app) is built on
branch `allocation-request-plan`, not released; storage, billing and branding aren't started.

## The problem

A new allocation request is ColdFront's `AllocationCreateView` with its `AllocationForm`. Two
plugins each need more from some requests:

- **storage** needs a quota for Storage allocations;
- **billing** needs a quota, a cost center and grant, and optionally a no-cost quota for paid
  storage (`resource.requires_payment`).

Today each plugin replaces `coldfront.core.allocation.views.AllocationCreateView` with a subclass in
its `ready()`, and each subclass redirects the new allocation to its own second page to collect
these details. That causes four problems:

1. **Only one replacement can win.** It's whichever app comes last in `INSTALLED_APPS`. At Tufts
   that's billing, so storage's page is never used, and storage's receivers that filter on
   `sender=` its own view class never fire.
2. **Which page a request gets is decided by the plugin that wins, not by what the request needs.**
   Storage that isn't billed and billing for something that isn't storage (both coming soon at
   Tufts) don't fit either plugin's rule.
3. **The request is announced before it's complete.** ColdFront sends the admin email and
   `allocation_new` at the end of `form_valid`, before the redirect to the second page. Anything
   listening sees an allocation with no quota. The ticketing plugin files the admins' ServiceNow
   ticket at that moment, so the ticket has no quota in it.
4. **Abandoned requests leave things behind.** A user who cancels on the second page, or never
   finishes, leaves an incomplete New allocation, and with ticketing an open ticket for it.

## The idea

Add the plugins' fields to ColdFront's own request form, so a request is complete when it's
submitted.

`AllocationCreateView.form_valid` starts with `super().form_valid(form)`, which calls
`form.save()`, and ends by sending the admin email and `allocation_new`
(`coldfront/core/allocation/views.py`, lines 668-702 in ColdFront 1.1.10). A form whose `save()`
saves the allocation and then has each plugin save its part does all of that before the email and
the signal. So:

- there's no second page, no incomplete request, and nothing to cancel;
- the admin email, `allocation_new` and everything listening to it (ticketing, the blueprint, the
  storage plugin's receivers) see a complete allocation, at every site, whether or not
  `EMAIL_ENABLED` is on;
- ColdFront's view, form and signals are used unchanged. There's **no change to the ColdFront fork**
  and nothing copied from ColdFront.

## Design

A new app in this library, `coldfront_utils.allocation_request`, installed like `task_context`. It
overrides no templates (see "The page"), so its place in `INSTALLED_APPS` doesn't matter for them. It
does matter to receivers that filter on `sender=AllocationCreateView`: ColdFront's page sends
`allocation_new` and `allocation_activate_user` with `sender=self.__class__`, now this app's view, so
such a receiver fires only if its module was imported after the swap. Receivers should take any sender
and check it with `coldfront_utils.signals.sent_by` (ticketing's helper, moved here); a receiver
filtering on ColdFront's own view is the system check error E004, since it would never run. A plugin that still replaces the view stops startup
(`ImproperlyConfigured` if it did so earlier in `INSTALLED_APPS`, E002 if later).

### Sections

A plugin adds to the request form by registering a **section** in its `AppConfig.ready()`. A section
is a class with:

| Member | What it does |
|---|---|
| `name` | A stable identifier, e.g. `storage_quota`, `billing_cost`. Settings refer to it, and it's the section's form prefix, so it uses underscores, not dots (a dot in an element id reads as a class in CSS selectors and jQuery). |
| `order` | Where the section's card goes, after ColdFront's own fields. |
| `form_class` | A Django form with the section's fields. Built with `project` and `user` and the section's prefix, so sections' fields can't collide with each other or with ColdFront's. |
| `__init__(resource, project, user, request)` (inherited) | The form builds each section for one request, with its details, which the methods read from `self`. A section may keep what it looks up on `self` for the rest of the request (an account found in `applies()`, used in `save()`). A new detail later is a new attribute, not a change to every plugin's method signatures. |
| `applies()` | Whether this request needs the section. Decided from the chosen resource, because no allocation exists while the form is filled in. |
| `save(allocation, form)` | Saves the section's part from its validated form. Runs inside `AllocationRequestForm.save()`, after the allocation is saved. It must not fail on anything a user or an admin can get wrong: those are checked in the section form's validation or by a system check (see "Saving"). |
| `template` | The plugin's own template for its card: heading, intro text and fields, as the plugin wants them laid out. It extends this app's `allocation_request/card.html` (see "Templates"). Rendered with the section's form, the chosen resource and project, and whatever `card_context()` adds. |
| `card_context()` (optional) | Extra data for the card's template that depends on the request, such as billing's no-cost quota left for the resource and its price per TB. |

Registration is `register_request_section(SectionClass)`. A setting,
`ALLOCATION_REQUEST_SECTIONS`, can list the sections to use and their order. If it's unset, every
registered section is used, sorted by `order` and then by registration, so installing a plugin is
enough. An unknown name
in the setting is a system check error, so a typo doesn't silently drop a section. (Django runs
system checks with management commands such as `migrate`, which Tufts' deploy runs every time, not
when the app server starts.)

A registry for which failed tasks may be re-run has also been proposed for this library, on the same
pattern (plugins register in `ready()`, settings override). If both are built, they should share one
implementation.

### The form and the view

- **The form.** `AllocationRequestForm` subclasses ColdFront's `AllocationForm`. Section fields are
  **not** added to its own fields: each section that applies to the chosen resource gets its own
  prefixed form (`form_class`), held by `AllocationRequestForm` in `section_forms`. So ColdFront's
  `{{ form|crispy }}` still renders ColdFront's fields and nothing more, and a section's fields appear
  only in its card.
  - Which sections apply is decided from the resource: the posted one when the form is bound, else
    the initial resource, or, like the drop-down, the first the user can request. The posted value is
    read the way ColdFront's resource field reads it, so the sections match the resource that's
    saved. If the resource itself is invalid, no section applies, ColdFront's own checks are skipped
    (they'd crash without a resource), and the resource field's error shows.
  - `clean()` validates each section form and, if any is invalid, adds one summary error to
    `request_sections` (see "Validation"), so the form is invalid and `CreateView` re-renders the page
    as usual. The per-field errors stay on the section forms, shown in their cards.
  - ColdFront's own `clean()` sets the status last, so when it refuses a request (the allocation
    limit, a missing account) the allocation has none, and Django's model validation would crash on
    it with a 500. The form gives it a status that's never saved, so those show as form errors.
  - `save()` saves the allocation as `AllocationForm` does, then calls each applicable section's
    `save()`.
- **Validation.** Two places, and the server is the one that counts.
  - *In the browser.* Each card renders its section form's fields with their HTML constraints
    (`required`, `type="number"`, `min`, `step`), so the browser won't submit while one fails and
    points at the field. Only the applicable cards are on the page, so a section that doesn't apply
    can't block the submit. This is a convenience: it can be bypassed (JavaScript off and a stale
    card, a crafted request).
  - *On the server, on every submit.* `CreateView` calls `form.is_valid()` before saving anything.
    ColdFront's own fields and `clean()` run first. Then `AllocationRequestForm.clean()` takes the
    **posted** resource (not whichever cards were on the page), builds a bound form for each section
    that applies to it, and validates them; a section form's `clean()` gets the resource, project
    and user for checks across fields (billing's "a grant needs a cost center"). If any section form
    is invalid, `clean()` adds one error to `request_sections` ("Please correct the errors below") so
    the main form is invalid, and the per-field messages stay on the section forms.
  - *An invalid submit saves nothing:* `form_invalid` re-renders the page, with no allocation, no
    admin email and no `allocation_new`. ColdFront's fields come back as filled in, with their errors,
    and the cards for the posted resource come back from the bound section forms, with the typed
    values and each error at its field. Only when ColdFront's form and every applicable section form
    are valid does `form_valid` run.
  - Values posted for sections that don't apply to the posted resource are never read: their forms
    aren't built. Without JavaScript, a resource changed on the page comes back as the new resource's
    "This field is required", with the right cards. Billing's estimate failing to load is a display
    problem only and doesn't block the submit, as on billing's page today.
- **The resource drop-down.** `AllocationRequestForm.__init__` sets `hx-get` (the request page's own
  URL, see below), `hx-trigger="change"`, `hx-target` (the container), `hx-swap="outerHTML"` and
  `autocomplete="off"` on the `resource` field's widget; htmx sends only the drop-down's own value,
  so a section's form can't depend on other request fields while the cards are fetched.
  `autocomplete="off"` stops a browser restoring a different choice on reload, which would show it
  with the cards rendered for another resource. Both ColdFront's page (crispy) and Tufts' (by hand) render that widget,
  so both get the attributes without editing either. The drop-down is a plain `<select>` (ColdFront
  only applies select2 to `.fos-select2`), so htmx sees its native `change` event; the page's existing
  jQuery handler for quantity, description and EULA keeps working beside it.
- **The view.** The app replaces `coldfront.core.allocation.views.AllocationCreateView` in its
  `ready()` with a subclass. ColdFront's `urls.py` reads `allocation_views.AllocationCreateView` when
  it's imported, after every app's `ready()`, so the URL uses the subclass. The subclass:
  - sets `form_class` to `AllocationRequestForm`;
  - answers an htmx GET of the same URL, with the chosen resource, by rendering just the container
    with the cards for that resource. It's the request page's own view, so the cards go through the
    same `test_func` (`ProjectPermission.UPDATE`) and `dispatch` (project not needing review, Active
    or New) as the page, by construction; a resource the user can't request
    (`get_user_resources`) renders no cards. When the page would redirect instead (an expired
    session, a project that needs review or is archived), the card request gets htmx's `HX-Refresh`,
    so the browser reloads the page and follows the redirect itself, rather than htmx swapping the
    destination page into the cards;
  - doesn't override `form_valid`.
- **The project** is the one in the URL: the form disables its hidden `project` field, so a posted
  value is ignored. The page's permission check and the sections ran for that project, and ColdFront's
  own form would otherwise save the allocation to whatever project was posted.

  Storage and billing stop replacing the view, so there's one replacement and `INSTALLED_APPS` order
  no longer matters.
- **Saving.** A section's `save()` runs before ColdFront adds the resource, the PI and users
  (`resources.add` and `add_user` come after `form.save()` in `form_valid`), so sections take the
  resource from their argument, not from `allocation.resources`. Nothing in core or the plugins
  reacts to an `AllocationAttribute` being saved, so creating attributes this early is safe.
  `AllocationRequestForm.save()` wraps the allocation and its sections' saves in
  `transaction.atomic()`, so a section that fails rolls back the allocation too. That covers only
  `form.save()`: the rest of ColdFront's `form_valid` (users, resource, email, `allocation_new`) runs
  after it, outside, as today. Widening the transaction to all of `form_valid` was considered and
  set aside: receivers of `allocation_new` that call `async_task` directly, such as the blueprint's,
  would then queue tasks a worker could run before the allocation was committed. So sections don't
  queue tasks from `save()`; work that needs the finished request belongs in an `allocation_new`
  receiver. A section that fails while saving is a 500 for the user, so anything
  that can go wrong is checked before: in the section form's validation, or by a system check (for
  example, that the `Storage Quota (TB)` allocation attribute type exists, which billing's page
  catches at save time today).

### The page

- **The container comes from the form.** `AllocationRequestForm` has one more member,
  `request_sections`: a field that takes no input, whose widget renders the container
  (`allocation_request/sections.html`) with the cards for the current resource. It's placed last
  among ColdFront's fields.
  - ColdFront's own page renders `{{ form|crispy }}`, so the container appears after ColdFront's
    fields with no template change.
  - A page that renders fields by hand, as Tufts' coldfront-branding does, adds
    `{{ form.request_sections }}` where the cards should go, after "Reason for Request".
  - So the app overrides no template and copies nothing from ColdFront. A system check can warn when
    sections are registered but the page in use renders neither the whole form nor
    `request_sections`.
- **Showing the right sections: htmx.** Changing the resource asks the page's view for that
  resource's cards (see "The view"), and htmx swaps them into the container. Only applicable sections
  are ever on the page, so there are no hidden fields (whose `required` or `min` attributes would
  still block the browser's submit) and no per-resource data to embed for a script to rebuild text
  from.
  - The first render and a failed submit come from the server too: the page renders the cards for
    the selected resource, and a submit that fails validation comes back with the cards for the
    posted resource, filled in, with their errors.
  - Card scripts, such as billing's cost center autocomplete and estimate, start on the `htmx:load`
    event on `document` as well as on page load. ColdFront's bundle imports htmx as a module and
    doesn't set a global `htmx`, so `htmx.onLoad` isn't available to page scripts. Bootstrap's
    JavaScript isn't reachable from page scripts either, so cards don't use popovers or tooltips.
  - Without JavaScript, the cards are those for the resource the page loaded with. Changing the
    resource doesn't update them, but validation runs only the sections for the submitted resource,
    so a mismatch comes back as a form error.
  - Switching the resource re-renders the cards, so values typed for one resource are lost when
    switching away and back. Accepted.
  - ColdFront already ships htmx and `django_htmx`, and loads htmx on every page from
    `common/base.html`; no new dependency.

### Templates

**Each plugin owns its card's template.** The section's `template` lives in the plugin
(`storage/allocation_request/quota.html`, `coldfront_billing/allocation_request/cost.html`) and
lays out the card's heading, intro text and fields as that plugin needs: storage's is a heading, a
sentence and one field; billing's adds the cost center autocomplete, the no-cost quota checkbox and
the live estimate, with its own script.

- **One look.** The card templates extend this app's `allocation_request/card.html`, which draws the
  Bootstrap card (as in the chosen mockup) and marks it with the section's name. Plugins fill its
  blocks, `heading`, `intro` and `fields`, so every card looks the same without each plugin copying
  the markup.
- **The site's wording.** Each card puts its intro text in a small template of its own (for example
  `storage/allocation_request/quota_intro.html`), included from the `intro` block. A site changes the
  wording by overriding just that template in its branding, without copying the card, so Tufts can
  name its own pages and policies without changing the plugin. A site that wants a different card
  overrides the card template itself.
- **Per-resource text.** ColdFront's `form_description` resource attribute already shows text for
  the chosen resource. Because cards are rendered for the chosen resource, a card's template can also
  vary its text by resource, from its `card_context()` (billing's price per TB is one).

### The sections Tufts needs

- **`storage_quota`** (storage plugin). Applies to Storage resources whose `quota_on_request`
  resource attribute is Yes (see "Which resources ask for a quota", below). One field, the quota in
  the plugin's units, minimum 0.1. Saves the `Storage Quota (TB)` allocation attribute, as storage's
  page does today.
- **`billing_cost`** (billing plugin). Applies to resources that require payment. Fields: cost center
  and grant (shown only when the project has no cost center assignment yet, as today), and the
  no-cost quota to apply. Saves the cost center assignment and the `allocation_no_cost_quota`
  attribute, as billing's page does today, with the same permission check for applying a no-cost
  quota. Its card shows the no-cost quota choice (a checkbox, only when the user has no-cost quota
  for the resource) and the cost estimate inline, updating as the quota changes; billing's current
  confirmation dialog on Submit goes away. The estimate needs the quota, so the card's script reads
  the `storage_quota` section's quota field by its id. That id (from the section's prefix and field
  name) becomes a constant in this library, so billing doesn't import storage.

Today billing's page also asks for the quota. With both sections, the quota is asked for once, by
`storage_quota`. A billed resource that isn't storage would get its own section for whatever it's
billed on, and billing's cost preview would need to handle it.

### Which resources ask for a quota

Whether a request asks for a quota is a property of the resource, not of its type or of whether it's
paid: some unpaid storage should ask and some shouldn't. A Yes/No resource attribute decides it,
`quota_on_request`. Its name comes from a storage setting, defaulting to `quota_on_request`, the way
billing makes its resource attribute names (`cost_per_terabyte`, `no_cost_quota_type`) configurable.
This follows ColdFront's own use of resource attributes to shape the request form
(`quantity_label`, `quantity_default_value`, `form_description`).

- **Yes:** the request form shows the quota field for this resource, and requires it.
- **No, or no attribute:** no quota field. Opting in is explicit, so a new resource doesn't start
  asking customers for a quota by accident. The cost is a deploy step: every resource that should ask
  has to have the attribute before the switch (see "Build order and deploy").

The same approach can later carry the field's minimum or default per resource (for example
`quota_minimum`), as ColdFront's `quantity_default_value` does for quantity.

Tufts' Storage resources on p01 (2026-11-12). All are requestable, and no allocation blueprint sets a
quota for any of them, so a resource that doesn't ask has no quota until an admin sets one.

| Resource | Paid | `quota_on_request` |
|---|---|---|
| Tier 1 HPC | yes | Yes |
| Tier 1 NFS Rstore | yes | Yes |
| Tier 1 Other | yes | Yes |
| Tier 1 RStore | yes | Yes |
| Tier 2 Research | yes | Yes |
| Tier 3 Research | yes | Yes |
| Course - Tier 1 HPC | no | to decide |
| Course - Tier 1 RStore | no | to decide |
| TTS Staff - Tier 1 HPC | no | to decide |
| TTS Staff - Tier 1 RStore | no | to decide |
| TTS Staff - Tier 2 Research | no | to decide |
| TTS Staff - Tier 3 Research | no | to decide |

Paid storage must say Yes: billing's cost preview and charges need the quota.

## Changes by repo

| Repo | Change |
|---|---|
| coldfront-utils | The `allocation_request` app: section registry, setting and system checks, `AllocationRequestForm`, the view replacement, the view's htmx response with a resource's cards, the `request_sections` field and its widget, `allocation_request/sections.html` and `allocation_request/card.html`, and the system checks. A constant for the quota field's id. |
| coldfront-storage-plugin | Change its receivers that filter on `sender=AllocationCreateView` (`add-requester-info`, `add_storage_attributes_to_new_allocation`) to take any sender and check it with `coldfront_utils.signals.sent_by` (E004 fails `migrate` and `check` until they do). Register `storage_quota`, with its card template and intro template, applying where the resource's `quota_on_request` attribute is Yes; a setting for the attribute's name, and a data migration creating its type. Stop replacing `AllocationCreateView`; remove `StorageAllocationRequestDetailsView`, its form, URL and template. |
| coldfront-billing-plugin | Register `billing_cost`, with its card template, intro template and the card's script (autocomplete, no-cost quota, live estimate), started on `htmx:load`. Stop replacing `AllocationCreateView`; remove `UpdateAllocationDetailsView` and `DeleteIncompleteAllocationView`, their URLs and template. Change the cost preview endpoint to take a project and resource (and quota) instead of an allocation pk. |
| coldfront-ticketing-plugin | Import `sent_by` from `coldfront_utils.signals` and drop its own copy in `ticketing/signals.py` (once its lock has a coldfront-utils with it). Otherwise none: the request ticket is filed on `allocation_new`, as now, and the quota is there. |
| coldfront-ansible | Add `coldfront_utils.allocation_request` to `INSTALLED_APPS`. |
| coldfront-branding | Add `{{ form.request_sections }}` to its `allocation/allocation_create.html`, after "Reason for Request". Tufts' wording for the sections' intro text, as overrides of the plugins' intro templates, if the plugins' defaults don't fit. |
| coldfront-tufts (fork) | None. |

## Build order and deploy

1. coldfront-utils: the app, test-first, with a test project that registers sample sections. Release
   it (a version bump, as for 0.2.0).
2. Storage and billing: their sections, test-first, against that release. Each plugin's old view and
   page are removed in the same change.
3. Before the switch, on each host: create the `quota_on_request` resource attribute type (Yes/No)
   and set it on the resources in the table under "Which resources ask for a quota". A data migration
   in the storage plugin can create the type; which resources say Yes is each site's choice, so that
   part is done by hand or with a small management command.
4. Lock all of them in the fork's `uv.lock` together, and deploy with the ansible change in the same
   run, first to s01.

Storage, billing, the branding change and the ansible change must reach a host together (branding
is deployed at its own ref, so that ref has to move in the same run): without `request_sections`, Tufts'
page wouldn't show the cards, so a paid storage request couldn't be submitted at all (the quota card
it needs isn't there) and the page wouldn't say why; W002 and the view's logged error point at
this. With the new app but an old
plugin, two view replacements would compete again; with new plugins but without the app, storage
and billing requests would have nowhere to enter a quota. A system check in each plugin can stop
startup if its section is registered but the app isn't installed. An old plugin listed before the
app stops startup with `ImproperlyConfigured`; one listed after it is E002.

## Open questions

1. **Unpaid storage.** Decided: the `quota_on_request` resource attribute (see "Which resources ask
   for a quota"). Still to decide: which of the six unpaid resources (Course and TTS Staff) say Yes.
2. **Billed resources that aren't storage.** What does billing need for them, and what does the cost
   preview show?
3. **Incomplete requests that already exist.** New allocations made through the current second
   pages that never got a quota. Find them, then complete or delete them by hand before the switch?
4. **Allocation change requests.** Billing also replaces `AllocationChangeView` to collect a no-cost
   quota with a quota change. The same section idea could cover change requests later; this plan is
   for new requests only.
5. **The form's look.** Decided 2026-11-12 from mockups: each section in a Bootstrap card (header
   with the section's heading, intro text in the body), and billing's no-cost quota choice and cost
   estimate inline in its card, updating as the quota changes, instead of a confirmation on Submit.
   The default intro wording in the mockups reads fine at 1140px and 820px.
6. **Admin requests.** Should admins skip any section (for example cost center), or fill it in like
   everyone else?

## Considered and set aside

- **Keep a second page, owned by this library, that sections register on.** The request would be
  incomplete until that page was saved, so the library would need a completion record, a Cancel
  button, a new `allocation_request_completed` signal for listeners such as ticketing, and a way to
  find abandoned requests. And ColdFront would still send the admin email and `allocation_new` at
  creation, before the details, unless the fork changed.
- **Change the fork** to move the email and `allocation_new` into a method a subclass can defer. That
  works, but adds a change to maintain against upstream. If upstream ColdFront adds such a hook, it
  would be worth using; this plan doesn't need it.
- **Copy ColdFront's `form_valid` into a subclass** without the email and signal. It would drift from
  ColdFront's code silently on upgrades.
- **Make storage's view the live one.** It fixes who owns the flow for storage, but not billing for
  other resources or unbilled storage, and billing's fields would still need a home.
