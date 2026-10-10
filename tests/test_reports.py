from datetime import date

from src.models import Companion, DailyEntry
from src.reports import build_period_summary, list_available_months, month_bounds


def test_sums_sales_within_the_period():
    entries = [
        DailyEntry(date=date(2026, 7, 29), customer_names=["田中"], is_own_customer=True, new_or_existing="既存", sales_amount=5000),
        DailyEntry(date=date(2026, 8, 15), customer_names=["佐藤"], is_own_customer=True, new_or_existing="既存", sales_amount=3000),
        DailyEntry(date=date(2026, 8, 31), customer_names=["鈴木"], is_own_customer=True, new_or_existing="既存", sales_amount=9000),
    ]

    summary = build_period_summary(entries, start_date=date(2026, 7, 29), end_date=date(2026, 8, 30))

    assert summary.total_sales == 8000  # 8/31分は期間外なので含まない


def test_other_customers_sales_are_also_counted():
    """他客でも売上を分けてもらえる場合があるため、自客・他客を問わず合算する"""
    entries = [
        DailyEntry(date=date(2026, 8, 1), customer_names=["田中"], is_own_customer=False, new_or_existing="既存", sales_amount=5000),
    ]

    summary = build_period_summary(entries, start_date=date(2026, 7, 29), end_date=date(2026, 8, 30))

    assert summary.total_sales == 5000


def test_accompanied_flag_drives_the_count_not_having_companions():
    entries = [
        # 同伴者(枝)がいても、同伴フラグが立っていなければカウントしない
        DailyEntry(
            date=date(2026, 8, 3), customer_names=["佐藤"], is_own_customer=True, new_or_existing="既存",
            accompanied=False, companions=[Companion("伊藤")],
        ),
        # 同伴フラグが立っていれば、同伴者(枝)がいなくてもカウントする
        DailyEntry(
            date=date(2026, 8, 10), customer_names=["田中"], is_own_customer=True, new_or_existing="既存",
            accompanied=True,
        ),
    ]

    summary = build_period_summary(entries, start_date=date(2026, 7, 29), end_date=date(2026, 8, 30))

    assert summary.accompanied_count == 1
    assert summary.accompanied_visits[0].customer_names == ["田中"]


def test_accompanied_visit_keeps_staff_in_charge_for_other_customer():
    entries = [
        DailyEntry(
            date=date(2026, 8, 10), customer_names=["田中"], is_own_customer=False, new_or_existing="既存",
            staff_in_charge="山田", accompanied=True,
        ),
    ]

    summary = build_period_summary(entries, start_date=date(2026, 7, 29), end_date=date(2026, 8, 30))

    assert summary.accompanied_visits[0].staff_in_charge == "山田"


def test_accompanied_visits_are_sorted_by_date():
    entries = [
        DailyEntry(date=date(2026, 8, 20), customer_names=["田中"], is_own_customer=True, new_or_existing="既存", accompanied=True),
        DailyEntry(date=date(2026, 8, 3), customer_names=["佐藤"], is_own_customer=True, new_or_existing="既存", accompanied=True),
    ]

    summary = build_period_summary(entries, start_date=date(2026, 7, 29), end_date=date(2026, 8, 30))

    assert [v.date for v in summary.accompanied_visits] == [date(2026, 8, 3), date(2026, 8, 20)]


def test_entries_outside_period_are_ignored():
    entries = [
        DailyEntry(
            date=date(2026, 7, 28), customer_names=["田中"], is_own_customer=True, new_or_existing="既存",
            accompanied=True, sales_amount=1000,
        ),
    ]

    summary = build_period_summary(entries, start_date=date(2026, 7, 29), end_date=date(2026, 8, 30))

    assert summary.total_sales == 0
    assert summary.accompanied_count == 0


def test_period_boundaries_are_inclusive():
    entries = [
        DailyEntry(date=date(2026, 7, 29), customer_names=["田中"], is_own_customer=True, new_or_existing="既存", sales_amount=1000),
        DailyEntry(date=date(2026, 8, 30), customer_names=["佐藤"], is_own_customer=True, new_or_existing="既存", sales_amount=2000),
    ]

    summary = build_period_summary(entries, start_date=date(2026, 7, 29), end_date=date(2026, 8, 30))

    assert summary.total_sales == 3000


def test_month_bounds_covers_whole_calendar_month():
    assert month_bounds(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))  # 2026年は平年
    assert month_bounds(2026, 8) == (date(2026, 8, 1), date(2026, 8, 31))


def test_list_available_months_returns_distinct_sorted_months():
    entries = [
        DailyEntry(date=date(2026, 8, 5), customer_names=["田中"], is_own_customer=True, new_or_existing="既存"),
        DailyEntry(date=date(2026, 6, 1), customer_names=["佐藤"], is_own_customer=True, new_or_existing="既存"),
        DailyEntry(date=date(2026, 8, 20), customer_names=["鈴木"], is_own_customer=True, new_or_existing="既存"),
    ]

    assert list_available_months(entries) == [(2026, 6), (2026, 8)]


def test_list_available_months_empty_when_no_entries():
    assert list_available_months([]) == []
