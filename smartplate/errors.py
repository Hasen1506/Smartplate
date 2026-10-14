"""What an error may tell the person who caused it (grill #65).

Ziggy's own `raise ValueError("…")` lines are written for people, so their text is shown.
Anything else that happens to be a ValueError (a JSON or date parser, int("abc"), a
database driver) can carry internals, so the person gets a generic sentence and a
request id, and the details go to the server log under that id.

New code should raise `UserError` for text meant for people. Existing `raise ValueError`
lines inside the smartplate package keep working: `public_message` treats an explicit
`raise` statement in our own source as a message written for people.
"""
import os
import re
import secrets
import traceback

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERIC = "That request couldn't be used. Check what you entered and try again."
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class UserError(ValueError):
    """A problem with the request, in words written for the person who made it."""


def request_id(incoming: str | None = None) -> str:
    """The caller's X-Request-ID when it is a sane token (Render forwards one), else a new one."""
    if incoming and _REQUEST_ID.match(incoming):
        return incoming
    return secrets.token_hex(8)


def public_message(error: BaseException) -> str | None:
    """The error's own text when it was written for people, else None."""
    if isinstance(error, UserError):
        return str(error) or None
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    if not frames:
        return None
    last = frames[-1]
    ours = os.path.abspath(last.filename).startswith(PACKAGE_DIR + os.sep)
    explicit = (last.line or "").lstrip().startswith("raise")
    if ours and explicit and str(error):
        return str(error)
    return None
