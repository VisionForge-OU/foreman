"""Daily cost ledger for the global hard-stop ceiling (R5/§9).

Accumulated spend is persisted per UTC day in ``.foreman/daily_cost.json`` so the
ceiling survives restarts (R4). When the day's total reaches the configured
``daily_cost_usd``, the scheduler stops launching new workers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class CostLedger:
    def __init__(self, path: Path | str, day_fn: Callable[[], str] = _today):
        self.path = Path(path)
        self._day_fn = day_fn

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}

    def spent_today(self) -> float:
        return float(self._load().get(self._day_fn(), 0.0))

    def add(self, amount: float) -> float:
        data = self._load()
        day = self._day_fn()
        data[day] = round(float(data.get(day, 0.0)) + float(amount), 6)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))
        return data[day]

    def would_exceed(self, ceiling: float) -> bool:
        return self.spent_today() >= ceiling
