import re
from datetime import datetime
from typing import List

from .event import Event


def parse_entries(entries: List[dict], profile: dict, batch_id: int) -> List[Event]:
    rx = re.compile(profile["line_regex"])
    ts_format = profile.get("ts_format")
    level_map = profile.get("level_map", {})
    reserved = {"ts", "level", "component", "message", "category"}

    events: List[Event] = []
    for entry in entries:
        m = rx.match(entry["raw"].strip())
        if not m:
            continue
        g = m.groupdict()

        ts_raw = g.get("ts") or ""
        ts_utc = None
        if ts_raw and ts_format:
            try:
                ts_utc = datetime.strptime(ts_raw, ts_format)
            except ValueError:
                ts_utc = None

        level_raw = (g.get("level") or "").upper()
        level = level_map.get(level_raw, "UNKNOWN")

        attributes = {
            k: v
            for k, v in g.items()
            if k not in reserved and v is not None
        }

        events.append(
            Event(
                batch_id=batch_id,
                line_no=entry["line_no"],
                ts_utc=ts_utc,
                ts_raw=ts_raw,
                level=level,
                category=g.get("category"),
                component=g.get("component") or "",
                message=g.get("message") or "",
                raw=entry["raw"],
                attributes=attributes,
            )
        )
    return events
