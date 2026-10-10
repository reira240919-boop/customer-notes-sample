"""顧客マスタからリマインド対象（連絡すべき客・誕生日が近い客）を抽出するロジック"""
from __future__ import annotations

from datetime import date

from .models import CustomerRecord


def find_reminder_targets(
    master: dict[str, CustomerRecord],
    today: date,
    default_frequency_days: int,
) -> list[CustomerRecord]:
    """連絡先を持つ客のうち、個別の連絡頻度（未設定ならデフォルト日数）を超えて
    連絡していない客を抽出する。一度も連絡日が記録されていない客は常に対象とする。
    """
    targets = []
    for record in master.values():
        if not record.has_contact:
            continue
        frequency = record.contact_frequency_days if record.contact_frequency_days is not None else default_frequency_days
        if record.last_contact_date is None:
            targets.append(record)
            continue
        elapsed_days = (today - record.last_contact_date).days
        if elapsed_days >= frequency:
            targets.append(record)
    return targets


def find_upcoming_birthdays(
    master: dict[str, CustomerRecord],
    today: date,
    days_ahead: int,
) -> list[CustomerRecord]:
    """誕生日（"MM-DD"形式）が today から days_ahead 日以内に来る客を抽出する"""
    targets = []
    for record in master.values():
        if not record.birthday:
            continue
        next_birthday = _next_occurrence(record.birthday, today)
        if next_birthday is None:
            continue
        if 0 <= (next_birthday - today).days <= days_ahead:
            targets.append(record)
    return targets


def _next_occurrence(birthday_mmdd: str, today: date) -> date | None:
    try:
        month, day = (int(part) for part in birthday_mmdd.split("-"))
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if candidate < today:
        candidate = date(today.year + 1, month, day)
    return candidate
