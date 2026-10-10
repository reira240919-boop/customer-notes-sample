"""名前で日次入力を検索し、来店日ごとの同席者（幹・枝を問わず）を調べるロジック"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import DailyEntry


@dataclass
class VisitOccurrence:
    date: date
    companions: list[str]  # その日、一緒にいた他の人（幹・枝を問わず、自分以外全員）
    accompanied: bool  # その来店が「同伴」だったか
    accompany_note: str  # 同伴の詳細（お店の場所など）
    sales_amount: int | None  # その来店の売上（無ければNone）


def find_visit_history(entries: list[DailyEntry], name: str) -> list[VisitOccurrence]:
    """指定した名前が登場する日次入力を日付順に集め、各来店の同席者・同伴有無・売上を返す"""
    history = []
    for entry in entries:
        all_names = entry.customer_names + [c.name for c in entry.companions]
        if name not in all_names:
            continue
        companions = [n for n in all_names if n != name]
        history.append(VisitOccurrence(
            date=entry.date,
            companions=companions,
            accompanied=entry.accompanied,
            accompany_note=entry.accompany_note,
            sales_amount=entry.sales_amount,
        ))

    return sorted(history, key=lambda occurrence: occurrence.date)
