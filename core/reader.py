import re
from typing import List, Optional


def read_entries(text: str, continuation_regex: Optional[str] = None) -> List[dict]:
    """Split raw log text into entries.

    Each entry is a dict with 'line_no' (1-based start line) and 'raw' (full text).
    Lines matching continuation_regex are appended to the previous entry.
    """
    lines = text.splitlines()
    cont = re.compile(continuation_regex) if continuation_regex else None

    entries: List[dict] = []
    for idx, line in enumerate(lines):
        if cont and entries and cont.match(line):
            entries[-1]["raw"] += "\n" + line
            continue
        entries.append({"line_no": idx + 1, "raw": line})
    return entries
