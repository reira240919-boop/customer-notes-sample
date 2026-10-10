"""今の期間（開始日〜締め日）の同伴回数・売上を日次入力から集計するロジック

「同伴者（枝）」＝客と一緒に来店した人 と、「同伴」＝食事等を経て一緒に入店する接客行為
は別概念のため、集計は entry.accompanied（同伴フラグ）のみを基準にする。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from .models import DailyEntry


@dataclass
class AccompaniedVisit:
    date: date
    customer_names: list[str]
    staff_in_charge: str | None  # 他客のみ。自客の場合はNone（＝自分が同伴した）
    accompany_note: str = ""  # 同伴の詳細（お店の場所など）


@dataclass
class PeriodSummary:
    start_date: date
    end_date: date
    total_sales: int = 0
    accompanied_visits: list[AccompaniedVisit] = field(default_factory=list)

    @property
    def accompanied_count(self) -> int:
        return len(self.accompanied_visits)


def build_period_summary(entries: list[DailyEntry], start_date: date, end_date: date) -> PeriodSummary:
    summary = PeriodSummary(start_date=start_date, end_date=end_date)

    for entry in entries:
        if not (start_date <= entry.date <= end_date):
            continue
        if entry.sales_amount:
            summary.total_sales += entry.sales_amount
        if entry.accompanied:
            summary.accompanied_visits.append(
                AccompaniedVisit(
                    date=entry.date,
                    customer_names=list(entry.customer_names),
                    staff_in_charge=entry.staff_in_charge,
                    accompany_note=entry.accompany_note,
                )
            )

    summary.accompanied_visits.sort(key=lambda v: v.date)
    return summary


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """指定した暦月の1日と末日を返す（月ごとのグラフ・過去の実績で使う）"""
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start, end


def list_available_months(entries: list[DailyEntry]) -> list[tuple[int, int]]:
    """日次入力に登場する暦月（年, 月）を古い順に返す"""
    months = {(entry.date.year, entry.date.month) for entry in entries}
    return sorted(months)
