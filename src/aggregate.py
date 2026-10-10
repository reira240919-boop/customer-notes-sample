"""日次入力シートの内容を顧客マスタに集約するロジック"""
from __future__ import annotations

from datetime import date

from .models import CustomerRecord, DailyEntry


def aggregate_daily_entries(
    entries: list[DailyEntry],
    master: dict[str, CustomerRecord] | None = None,
) -> dict[str, CustomerRecord]:
    """日次入力を顧客マスタ（名前完全一致で名寄せ）に統合する。

    master を渡すとその場で更新して返す（既存の顧客マスタへの追記集約に対応）。
    """
    if master is None:
        master = {}
    next_id = _IdCounter(len(master) + 1)

    for entry in entries:
        introduced_by = "・".join(entry.customer_names) if entry.companions else None

        for name in entry.customer_names:
            _upsert(
                master, next_id, name, entry.date,
                is_own_customer=entry.is_own_customer,
                staff_in_charge=entry.staff_in_charge,
                new_or_existing=entry.new_or_existing,
                has_contact=entry.has_contact,
                last_contact_date=entry.last_contact_date,
                birthday=entry.birthday,
                memo=entry.memo,
                accompanied_today=entry.accompanied,
                introduced_by=None,
            )

        for companion in entry.companions:
            _upsert(
                master, next_id, companion.name, entry.date,
                is_own_customer=entry.is_own_customer,
                staff_in_charge=entry.staff_in_charge,
                new_or_existing=entry.new_or_existing,
                has_contact=companion.has_contact,
                last_contact_date=None,
                birthday=None,
                memo=entry.memo,
                accompanied_today=False,
                introduced_by=introduced_by,
            )

    return master


class _IdCounter:
    def __init__(self, start: int):
        self.value = start

    def next(self) -> str:
        customer_id = f"C{self.value:04d}"
        self.value += 1
        return customer_id


def _upsert(
    master: dict[str, CustomerRecord],
    next_id: _IdCounter,
    name: str,
    visit_date: date,
    is_own_customer: bool,
    staff_in_charge: str | None,
    new_or_existing: str,
    has_contact: bool | None,
    last_contact_date: date | None,
    birthday: str | None,
    memo: str,
    accompanied_today: bool,
    introduced_by: str | None,
) -> None:
    if name in master:
        record = master[name]
        record.visit_dates.append(visit_date)
        record.is_own_customer = is_own_customer
        record.new_or_existing = new_or_existing
        if staff_in_charge is not None:
            record.staff_in_charge = staff_in_charge
        if has_contact is not None:
            record.has_contact = has_contact
        if last_contact_date is not None:
            # 過去分の入力が後から来る場合もあるため、新しい方の日付を採用する
            if record.last_contact_date is None or last_contact_date > record.last_contact_date:
                record.last_contact_date = last_contact_date
        if birthday is not None:
            record.birthday = birthday
        if memo:
            record.memo = f"{record.memo}\n{memo}".strip()
        if accompanied_today:
            record.accompanied_dates.append(visit_date)
        if introduced_by is not None:
            record.introduced_by = introduced_by
    else:
        master[name] = CustomerRecord(
            customer_id=next_id.next(),
            name=name,
            is_own_customer=is_own_customer,
            staff_in_charge=staff_in_charge,
            visit_dates=[visit_date],
            new_or_existing=new_or_existing,
            has_contact=has_contact,
            last_contact_date=last_contact_date,
            birthday=birthday,
            memo=memo,
            accompanied_dates=[visit_date] if accompanied_today else [],
            introduced_by=introduced_by,
        )
