"""Exceptions raised by Ward.

Ward drives machines that are already broken, so failures must be loud and
specific. Every error below says which of the three things went wrong: the
action was not allowed, the target did not answer in time, or the target is
not reachable at all.
"""


class WardError(Exception):
    """Base class for every error Ward raises deliberately."""


class CapabilityError(WardError):
    """The target does not support the requested action.

    Raised instead of silently degrading. A driver that cannot run commands
    must raise this from ``exec`` rather than returning a fake result.
    """


class TargetTimeout(WardError):
    """The target did not respond within the allotted time.

    A hung target must surface as this error, never as a blocked call.
    """


class TargetUnavailable(WardError):
    """The target could not be reached at all (socket gone, VM not running)."""
