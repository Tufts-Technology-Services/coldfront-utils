from django.dispatch import Signal

# Defined here rather than in the plugins that send them, so other plugins can receive them without
# depending on those plugins.

# Sent by coldfront_account_signup_plugin's create_home task once an account is fully provisioned:
# home and scratch directories exist and the user is in the access group. Receivers get `username`.
#
# It is sent from a django-q worker, not the web process, so receivers run there. Sent with
# send_robust: provisioning has already happened by then, so a failing receiver is logged
# rather than failing the task -- a retried create_home would only fire the signal again.
hpc_account_provisioned = Signal()

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
#                              without one (such as missing information)
#
# They are sent from a django-q worker, with send_robust, after the state has been recorded: a
# receiver sees the allocation as the task left it, and a receiver that raises is logged rather than
# changing the task's outcome. A failed task still raises its own exception.
share_creation_successful = Signal()
share_creation_failed = Signal()
quota_update_successful = Signal()
quota_update_failed = Signal()
