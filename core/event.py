from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Event:
    batch_id: int
    line_no: int
    ts_utc: Optional[datetime]
    ts_raw: str
    level: str
    category: Optional[str]
    component: str
    message: str
    raw: str
    attributes: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "line_no": self.line_no,
            "ts_utc": self.ts_utc,
            "ts_raw": self.ts_raw,
            "level": self.level,
            "category": self.category,
            "component": self.component,
            "message": self.message,
            "raw": self.raw,
            "attributes": self.attributes,
        }
