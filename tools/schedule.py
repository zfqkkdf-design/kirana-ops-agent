"""Store-wide auto-send job schedules: weekly sales deck + weekly khata reminder.

Persisted as global OwnerPreference rows (not scoped to any one chat, since both
jobs go out to every known chat_id). bot.py registers a reschedule callback per
job at startup via set_reschedule_callback so a schedule change here takes
effect on the live job immediately, no restart needed.
"""
import re
from datetime import time as clock_time
from typing import Callable, Optional

from sqlmodel import select

from db.engine import get_session
from db.models import OwnerPreference

_GLOBAL_CHAT_ID = "__global__"

JOBS = {
    "weekly_deck": {
        "label": "Weekly sales deck",
        "day_key": "weekly_deck_day",
        "time_key": "weekly_deck_time",
        "default_day": 1,  # Monday
        "default_time": clock_time(hour=9, minute=0),
    },
    "khata_reminder": {
        "label": "Weekly khata reminder",
        "day_key": "khata_reminder_day",
        "time_key": "khata_reminder_time",
        "default_day": 5,  # Friday
        "default_time": clock_time(hour=18, minute=0),
    },
}

_WEEKDAY_NAMES = {
    "sunday": 0, "sun": 0,
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
}

_reschedule_callbacks: dict[str, Callable[[int, clock_time], None]] = {}


def set_reschedule_callback(job_name: str, callback: Callable[[int, clock_time], None]) -> None:
    """Called once per job by bot.py at startup so schedule changes apply to the live job."""
    _reschedule_callbacks[job_name] = callback


def _parse_day(day: str) -> int:
    day_norm = day.strip().lower()
    if day_norm in _WEEKDAY_NAMES:
        return _WEEKDAY_NAMES[day_norm]
    if day_norm.isdigit() and 0 <= int(day_norm) <= 6:
        return int(day_norm)
    raise ValueError(
        f"Unrecognized day '{day}'. Use a weekday name (e.g. 'wednesday') or 0-6 "
        "(telegram convention: 0=Sunday .. 6=Saturday)."
    )


def _parse_time(time_str: str) -> clock_time:
    match = re.match(
        r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", time_str.strip(), re.IGNORECASE
    )
    if not match:
        raise ValueError(f"Unrecognized time '{time_str}'. Use e.g. '12:40 pm' or '09:00'.")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Unrecognized time '{time_str}'. Use e.g. '12:40 pm' or '09:00'.")
    return clock_time(hour=hour, minute=minute)


def _day_name(day_num: int) -> str:
    return [k for k, v in _WEEKDAY_NAMES.items() if v == day_num and len(k) > 3][0].capitalize()


def get_job_schedule(job_name: str) -> tuple[int, clock_time]:
    """Read-only. Returns (day, time) in telegram.ext day convention, IST wall-clock."""
    job = JOBS[job_name]
    with get_session() as session:
        rows = session.exec(
            select(OwnerPreference).where(OwnerPreference.chat_id == _GLOBAL_CHAT_ID)
        ).all()
    values = {row.key: row.value for row in rows}
    day = int(values[job["day_key"]]) if job["day_key"] in values else job["default_day"]
    if job["time_key"] in values:
        hour_str, minute_str = values[job["time_key"]].split(":")
        time_val = clock_time(hour=int(hour_str), minute=int(minute_str))
    else:
        time_val = job["default_time"]
    return day, time_val


def _set_job_schedule(job_name: str, day: str, time_str: str) -> str:
    job = JOBS[job_name]
    try:
        day_num = _parse_day(day)
        time_val = _parse_time(time_str)
    except ValueError as exc:
        return f"REFUSED: {exc}"

    with get_session() as session:
        for key, value in (
            (job["day_key"], str(day_num)),
            (job["time_key"], f"{time_val.hour:02d}:{time_val.minute:02d}"),
        ):
            existing = session.get(OwnerPreference, (_GLOBAL_CHAT_ID, key))
            if existing:
                existing.value = value
                session.add(existing)
            else:
                session.add(OwnerPreference(chat_id=_GLOBAL_CHAT_ID, key=key, value=value))
        session.commit()

    callback = _reschedule_callbacks.get(job_name)
    if callback is not None:
        callback(day_num, time_val)

    return (
        f"Success: {job['label']} now auto-sends every {_day_name(day_num)} at "
        f"{time_val.strftime('%H:%M')} IST, effective immediately."
    )


def set_weekly_deck_schedule(day: str, time_str: str, chat_id: str) -> str:
    """Change when the weekly sales deck auto-sends to every shop chat, effective immediately.

    Args:
        day: Weekday name ("wednesday") or 0-6 (telegram convention: 0=Sunday .. 6=Saturday).
        time_str: Time of day in IST, e.g. "12:40 pm" or "09:00".
        chat_id: Telegram chat id (accepted for tool-call parity, the schedule is store-wide).

    Returns:
        "Success: ..." confirming the new schedule, or "REFUSED: ..." if day/time is invalid.
    """
    return _set_job_schedule("weekly_deck", day, time_str)


def set_khata_reminder_schedule(day: str, time_str: str, chat_id: str) -> str:
    """Change when the weekly khata dues reminder auto-sends to every shop chat, effective
    immediately. This reminder nags the shop owner about outstanding customer credit — it does
    not message the customers themselves.

    Args:
        day: Weekday name ("friday") or 0-6 (telegram convention: 0=Sunday .. 6=Saturday).
        time_str: Time of day in IST, e.g. "6 pm" or "18:00".
        chat_id: Telegram chat id (accepted for tool-call parity, the schedule is store-wide).

    Returns:
        "Success: ..." confirming the new schedule, or "REFUSED: ..." if day/time is invalid.
    """
    return _set_job_schedule("khata_reminder", day, time_str)
