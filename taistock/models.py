from dataclasses import dataclass


@dataclass(frozen=True)
class Quote:
    code: str
    name: str
    close: float
    volume: int
    date: str
    market: str
