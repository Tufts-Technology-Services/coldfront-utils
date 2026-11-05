"""The shape of a django-q task's result, shared by the plugins so the task report reads them all alike."""

# the state a task reports when it has scheduled its own retry; matches the storage plugin's
# ProvisionStatus.SCHEDULED, so the two read the same on the task report
SCHEDULED = "scheduled"


def build_task_result(state: str, reason: str = "", returned=None) -> dict:
    """Build a task's result: a "message" of "<state>:<reason>", plus anything else to carry.

    django-q stores what a task returns, and the task report shows its "message", so every task
    that builds its result here reads the same way there. `state` is any string, or a str enum
    whose str() is its value. A `returned` dict has its keys carried through, and any other value
    is kept under "result". "message" is reserved for the status and always wins, so a caller can
    rely on it being present and well formed whatever was returned.
    """
    if isinstance(returned, dict):
        result = dict(returned)
    elif returned is not None:
        result = {"result": returned}
    else:
        result = {}
    result["message"] = f"{state}:{reason}"
    return result
