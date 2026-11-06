from django.apps import AppConfig


class TaskContextConfig(AppConfig):
    """Installing this app connects the receivers that stamp tasks with the acting user.

    Install it in every process that queues or runs tasks: the web app and the django-q cluster
    share settings, so INSTALLED_APPS covers both.
    """

    name = "coldfront_utils.task_context"
    label = "coldfront_utils_task_context"
    verbose_name = "Task context"

    def ready(self):
        from . import receivers  # noqa: F401 -- connects them
