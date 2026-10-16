from django.dispatch import Signal

# Defined here rather than in the plugin that sends it, so other plugins can receive it without
# depending on that plugin.

# Sent by coldfront_account_signup_plugin's create_home task once an account is fully provisioned:
# home and scratch directories exist and the user is in the access group. Receivers get `username`.
#
# It is sent from a django-q worker, not the web process, so receivers run there. Sent with
# send_robust: provisioning has already happened by then, so a failing receiver is logged
# rather than failing the task -- a retried create_home would only fire the signal again.
hpc_account_provisioned = Signal()
