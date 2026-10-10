from datetime import date

from src.models import CustomerRecord
from src.reminder import find_reminder_targets, find_upcoming_birthdays


def make_record(**overrides) -> CustomerRecord:
    defaults = dict(
        customer_id="C0001",
        name="田中",
        is_own_customer=True,
        has_contact=True,
        last_contact_date=date(2026, 7, 1),
        contact_frequency_days=None,
    )
    defaults.update(overrides)
    return CustomerRecord(**defaults)


def test_target_when_elapsed_days_exceeds_default_frequency():
    master = {"田中": make_record(last_contact_date=date(2026, 7, 1))}
    today = date(2026, 8, 1)  # 31日経過

    targets = find_reminder_targets(master, today, default_frequency_days=14)

    assert list(master.values())[0] in targets


def test_not_target_when_within_frequency():
    master = {"田中": make_record(last_contact_date=date(2026, 7, 25))}
    today = date(2026, 8, 1)  # 7日経過

    targets = find_reminder_targets(master, today, default_frequency_days=14)

    assert targets == []


def test_individual_frequency_overrides_default():
    master = {"田中": make_record(last_contact_date=date(2026, 7, 25), contact_frequency_days=5)}
    today = date(2026, 8, 1)  # 7日経過、個別設定5日を超えている

    targets = find_reminder_targets(master, today, default_frequency_days=30)

    assert len(targets) == 1


def test_no_contact_info_is_excluded():
    master = {"田中": make_record(has_contact=False, last_contact_date=None)}
    today = date(2026, 8, 1)

    targets = find_reminder_targets(master, today, default_frequency_days=14)

    assert targets == []


def test_never_contacted_customer_is_always_target():
    master = {"田中": make_record(has_contact=True, last_contact_date=None)}
    today = date(2026, 8, 1)

    targets = find_reminder_targets(master, today, default_frequency_days=14)

    assert len(targets) == 1


def test_upcoming_birthday_within_range():
    master = {"田中": make_record(birthday="08-10")}
    today = date(2026, 8, 1)

    targets = find_upcoming_birthdays(master, today, days_ahead=14)

    assert len(targets) == 1


def test_birthday_outside_range_excluded():
    master = {"田中": make_record(birthday="12-25")}
    today = date(2026, 8, 1)

    targets = find_upcoming_birthdays(master, today, days_ahead=14)

    assert targets == []


def test_birthday_wraps_to_next_year():
    master = {"田中": make_record(birthday="01-02")}
    today = date(2026, 12, 30)  # 年をまたいで3日後

    targets = find_upcoming_birthdays(master, today, days_ahead=5)

    assert len(targets) == 1
