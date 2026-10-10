from datetime import date

from src.models import Companion, DailyEntry
from src.search import find_visit_history


def test_trunk_customer_sees_companions_per_visit():
    entries = [
        DailyEntry(
            date=date(2026, 7, 30),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            companions=[Companion("鈴木")],
        ),
        DailyEntry(
            date=date(2026, 8, 5),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
        ),
    ]

    history = find_visit_history(entries, "田中")

    assert [h.date for h in history] == [date(2026, 7, 30), date(2026, 8, 5)]
    assert history[0].companions == ["鈴木"]
    assert history[1].companions == []


def test_companion_can_also_be_searched_and_sees_who_brought_them():
    entries = [
        DailyEntry(
            date=date(2026, 7, 30),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            companions=[Companion("鈴木")],
        )
    ]

    history = find_visit_history(entries, "鈴木")

    assert len(history) == 1
    assert history[0].companions == ["田中"]


def test_multiple_trunk_and_companions_are_all_visible_to_each_other():
    entries = [
        DailyEntry(
            date=date(2026, 7, 30),
            customer_names=["田中", "佐藤"],
            is_own_customer=True,
            new_or_existing="既存",
            companions=[Companion("鈴木"), Companion("伊藤")],
        )
    ]

    history = find_visit_history(entries, "佐藤")

    assert set(history[0].companions) == {"田中", "鈴木", "伊藤"}


def test_name_not_found_returns_empty_history():
    entries = [
        DailyEntry(date=date(2026, 7, 30), customer_names=["田中"], is_own_customer=True, new_or_existing="既存")
    ]

    assert find_visit_history(entries, "存在しない客") == []


def test_visit_includes_accompanied_flag_and_sales_amount():
    entries = [
        DailyEntry(
            date=date(2026, 7, 30),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            accompanied=True,
            sales_amount=30000,
        ),
        DailyEntry(
            date=date(2026, 8, 5),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
        ),
    ]

    history = find_visit_history(entries, "田中")

    assert history[0].accompanied is True
    assert history[0].sales_amount == 30000
    assert history[1].accompanied is False
    assert history[1].sales_amount is None


def test_results_are_sorted_by_date_regardless_of_input_order():
    entries = [
        DailyEntry(date=date(2026, 8, 5), customer_names=["田中"], is_own_customer=True, new_or_existing="既存"),
        DailyEntry(date=date(2026, 7, 30), customer_names=["田中"], is_own_customer=True, new_or_existing="既存"),
    ]

    history = find_visit_history(entries, "田中")

    assert [h.date for h in history] == [date(2026, 7, 30), date(2026, 8, 5)]
