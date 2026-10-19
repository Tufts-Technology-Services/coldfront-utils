from django.dispatch import Signal

# Defined here rather than in the plugins that send them, so other plugins can receive them without
# depending on those plugins.

# Sent by coldfront_account_signup_plugin's create_home task once an account is fully provisioned:
# home and scratch directories exist and the user is in the access group. Receivers get `username`,
# `home_path` and `scratch_path` (the account's cluster home and scratch directories), as plain
# strings: facts about the account, from which each receiver builds its own message. Anything else
# a receiver needs (the user's name, a login host) it looks up itself.
#
# It is sent from a django-q worker, not the web process, so receivers run there. Sent with
# send_robust: provisioning has already happened by then, so a failing receiver is logged
# rather than failing the task -- a retried create_home would only fire the signal again.
hpc_account_provisioned = Signal()

# Sent when an HPC account has been requested and can't be set up straight away, for anything that
# should know an account is on its way: a ticket telling the user, say. Receivers get `username`.
# Send it once per account; when the account is ready, hpc_account_provisioned follows, with the
# account's paths.
#
# Today's sender is coldfront_account_signup_plugin's create_home task, which sends it only when the
# user had no UID: the campus UID service had none for them and has just created one, and create_home
# will try again once it reaches the cluster. It isn't sent when a UID already existed and only hadn't
# reached the cluster yet. Sent from a django-q worker with send_robust, like hpc_account_provisioned:
# a failing receiver is logged rather than failing the task.
hpc_account_requested = Signal()

# Sent when setting up an HPC account fails, for anything that should tell admins: an admin-only
# ticket, say. Receivers get `username`, `message` (why it failed) and `exception` (the exception that
# stopped it, or None for a failure without one, such as the user not being added to the access group).
# Send it once per failed attempt; a later attempt that succeeds sends hpc_account_provisioned. Waiting
# for a UID to reach the cluster isn't a failure: that's hpc_account_requested's case.
#
# Today's sender is coldfront_account_signup_plugin's create_home task, from a django-q worker, with
# send_robust: a failing receiver is logged rather than replacing the task's own exception, which it
# still raises.
hpc_account_provisioning_failed = Signal()

# Sent by coldfront-storage-plugin when a share creation or quota update task reaches a final
# state: ProvisionStatus SUCCESSFUL or FAILED, in storage.constants. Nothing is sent for STARTED or
# SCHEDULED, because a task that has scheduled its own retry hasn't finished.
#
# The sender is the storage task function that ran. A receiver that wants every outcome connects
# without a sender and needs nothing from the storage plugin; only one that wants a particular task
# has to import it from there. Keyword arguments:
#
#   *_successful:  allocation  the Allocation the task ran for
#                  result      the dict the task returns; its "message" is "successful:<reason>"
#   *_failed:      allocation  the Allocation the task ran for
#                  message     why it failed, as recorded in the allocation's message attribute
#                  exception   the exception the task raised, or None for a failure recorded
#                              without one (such as no task or client configured)
#
# They are sent from a django-q worker, with send_robust, after the state has been recorded: a
# receiver sees the allocation as the task left it, and a receiver that raises is logged rather than
# changing the task's outcome. A failed task still raises its own exception.
share_creation_successful = Signal()
share_creation_failed = Signal()
quota_update_successful = Signal()
quota_update_failed = Signal()
