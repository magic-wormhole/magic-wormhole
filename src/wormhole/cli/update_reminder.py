import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from platformdirs import user_state_path

from .._version import get_versions


STALE_RELEASE_DAYS = 183
REMINDER_DAYS = 30
STATE_FILENAME = "pyapp-update-reminder.json"


def _parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_last_reminder(state_path):
    if not state_path.exists():
        return None
    with state_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    last_reminder = data.get("last_reminder")
    if last_reminder is None:
        return None
    return date.fromisoformat(last_reminder)


def _write_last_reminder(state_path, today):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as f:
        json.dump({"last_reminder": today.isoformat()}, f)
        f.write("\n")


def _default_state_path():
    return user_state_path(
        appname="magic-wormhole",
        appauthor=False,
    ) / STATE_FILENAME


def maybe_remind_about_pyapp_update(
        my_version,
        stderr,
        now=None,
        environ=None,
        state_path=None,
        release_date=None):
    """
    Remind PyApp users to manually check for updates when a release is old.

    PyApp sets ``PYAPP`` for launched applications. This function deliberately
    does not make any network requests; it only uses release metadata bundled
    into the installed package and a local state file.
    """
    environ = os.environ if environ is None else environ
    if "PYAPP" not in environ:
        return
    if "+" in my_version:
        return

    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    if release_date is None:
        release_date = get_versions().get("date")
    release_datetime = _parse_datetime(release_date)
    if release_datetime is None:
        return

    if (now - release_datetime).days <= STALE_RELEASE_DAYS:
        return

    try:
        today = now.date()
        state_path = (
            _default_state_path()
            if state_path is None else Path(state_path)
        )
        last_reminder = _load_last_reminder(state_path)
        if (last_reminder is not None and
                (today - last_reminder).days < REMINDER_DAYS):
            return
        _write_last_reminder(state_path, today)
    except Exception:
        return

    command = environ.get("PYAPP_COMMAND_NAME", "self")
    print(
        (
            "This Magic Wormhole release is more than six months old. "
            f"To check for a newer PyApp build, run: wormhole {command} update"
        ),
        file=stderr,
    )
