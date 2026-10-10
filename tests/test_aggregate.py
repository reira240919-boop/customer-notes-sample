from datetime import date

from src.aggregate import aggregate_daily_entries
from src.models import Companion, DailyEntry


def test_new_customer_creates_one_record():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="新規",
            has_contact=False,
            memo="ビール好き",
        )
    ]

    master = aggregate_daily_entries(entries)

    assert len(master) == 1
    record = master["田中"]
    assert record.customer_id == "C0001"
    assert record.visit_dates == [date(2026, 8, 1)]
    assert record.memo == "ビール好き"


def test_repeat_visit_accumulates_visit_history():
    entries = [
        DailyEntry(date=date(2026, 8, 1), customer_names=["田中"], is_own_customer=True, new_or_existing="既存"),
        DailyEntry(date=date(2026, 8, 5), customer_names=["田中"], is_own_customer=True, new_or_existing="既存"),
    ]

    master = aggregate_daily_entries(entries)

    assert master["田中"].visit_dates == [date(2026, 8, 1), date(2026, 8, 5)]


def test_group_visit_is_split_into_individual_records():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中", "鈴木"],
            is_own_customer=False,
            staff_in_charge="山田",
            new_or_existing="既存",
        )
    ]

    master = aggregate_daily_entries(entries)

    assert set(master.keys()) == {"田中", "鈴木"}
    assert master["田中"].staff_in_charge == "山田"
    assert master["鈴木"].staff_in_charge == "山田"


def test_aggregate_updates_existing_master():
    existing_entries = [
        DailyEntry(
            date=date(2026, 7, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            last_contact_date=date(2026, 7, 1),
        )
    ]
    master = aggregate_daily_entries(existing_entries)

    new_entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            last_contact_date=date(2026, 8, 1),
            accompanied=True,
        )
    ]
    aggregate_daily_entries(new_entries, master=master)

    record = master["田中"]
    assert record.visit_dates == [date(2026, 7, 1), date(2026, 8, 1)]
    assert record.last_contact_date == date(2026, 8, 1)
    assert record.accompanied_dates == [date(2026, 8, 1)]
    # 既存客の名寄せなのでIDは変わらない
    assert record.customer_id == "C0001"


def test_older_last_contact_date_does_not_overwrite_newer_one():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            last_contact_date=date(2026, 8, 1),
        )
    ]
    master = aggregate_daily_entries(entries)

    older_entry = [
        DailyEntry(
            date=date(2026, 8, 2),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            last_contact_date=date(2026, 6, 1),
        )
    ]
    aggregate_daily_entries(older_entry, master=master)

    assert master["田中"].last_contact_date == date(2026, 8, 1)


def test_companion_becomes_own_record_with_introduced_by():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            companions=[Companion("鈴木")],
        )
    ]

    master = aggregate_daily_entries(entries)

    assert set(master.keys()) == {"田中", "鈴木"}
    assert master["田中"].introduced_by is None
    assert master["鈴木"].introduced_by == "田中"
    assert master["鈴木"].visit_dates == [date(2026, 8, 1)]


def test_companion_does_not_inherit_trunk_customers_last_contact_or_birthday():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            has_contact=True,
            last_contact_date=date(2026, 8, 1),
            birthday="01-01",
            companions=[Companion("鈴木")],
        )
    ]

    master = aggregate_daily_entries(entries)

    assert master["田中"].has_contact is True
    assert master["鈴木"].last_contact_date is None
    assert master["鈴木"].birthday is None


def test_companion_has_contact_is_tracked_independently():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            has_contact=False,
            companions=[Companion("鈴木", has_contact=True)],
        )
    ]

    master = aggregate_daily_entries(entries)

    assert master["田中"].has_contact is False
    assert master["鈴木"].has_contact is True


def test_companion_does_not_get_accompanied_flag_on_own_record():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中"],
            is_own_customer=True,
            new_or_existing="既存",
            accompanied=True,
            companions=[Companion("鈴木")],
        )
    ]

    master = aggregate_daily_entries(entries)

    assert master["田中"].accompanied_dates == [date(2026, 8, 1)]
    assert master["鈴木"].accompanied_dates == []


def test_multiple_trunk_customers_recorded_together_for_companion():
    entries = [
        DailyEntry(
            date=date(2026, 8, 1),
            customer_names=["田中", "佐藤"],
            is_own_customer=True,
            new_or_existing="既存",
            companions=[Companion("鈴木")],
        )
    ]

    master = aggregate_daily_entries(entries)

    assert master["鈴木"].introduced_by == "田中・佐藤"
