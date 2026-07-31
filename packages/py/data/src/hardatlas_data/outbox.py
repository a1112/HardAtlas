from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: str
    topic: str
    aggregate_id: str
    payload: dict[str, object]
    occurred_at: datetime
