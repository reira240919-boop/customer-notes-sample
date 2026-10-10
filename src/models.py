"""日次入力・顧客マスタのデータ構造定義"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Companion:
    """同伴者（枝）1人分。名前と、その人自身の連絡先有無を持つ"""

    name: str
    has_contact: bool | None = None


@dataclass
class DailyEntry:
    """日次入力シートの1行（複数名で来店した場合は customer_names に複数格納）"""

    date: date
    customer_names: list[str]
    is_own_customer: bool  # True: 自客, False: 他客
    new_or_existing: str  # "新規" or "既存"
    staff_in_charge: str | None = None  # 他客のみ
    has_contact: bool | None = None  # 幹自身の連絡先有無
    last_contact_date: date | None = None  # 既存のみ：入力時点でわかる最終連絡日
    birthday: str | None = None  # "MM-DD" 形式
    memo: str = ""
    accompanied: bool = False  # 同伴の有無
    accompany_note: str = ""  # 同伴の詳細（お店の場所など）
    companions: list[Companion] = field(default_factory=list)  # 同伴者（枝）。customer_names（幹）が連れてきた客
    sales_amount: int | None = None  # 自客のみ：その来店の売上


SHARED_EVENT_KINDS = ["強制同伴日", "イベント", "ミーティング", "新調日", "連休"]
PLAN_KINDS = ["同伴", "来店"]


@dataclass
class CalendarEvent:
    """共通予定1件（強制同伴日・イベント・ミーティング・新調日・連休）。全員向け"""

    kind: str  # SHARED_EVENT_KINDS のいずれか
    start_date: date
    end_date: date
    title: str = ""  # イベントの種類名、連休の名称など（無ければ空文字）


@dataclass
class Plan:
    """個人の予定1件（同伴・来店）。来店記録として実際に入力されるまでの仮の予定"""

    date: date
    kind: str  # "同伴" or "来店"
    name: str
    recorded: bool = False  # 来店記録として入力済みか（済みでもカレンダーには残す）


@dataclass
class CustomerRecord:
    """顧客マスタの1行（お客様1人）"""

    customer_id: str
    name: str
    is_own_customer: bool
    staff_in_charge: str | None = None
    visit_dates: list[date] = field(default_factory=list)
    new_or_existing: str = "新規"
    has_contact: bool | None = None
    last_contact_date: date | None = None
    contact_frequency_days: int | None = None  # 個別設定。Noneならデフォルト値を適用
    birthday: str | None = None
    memo: str = ""
    accompanied_dates: list[date] = field(default_factory=list)
    introduced_by: str | None = None  # 紹介元（幹）。この客が誰かの同伴・紹介（枝）である場合の紹介者名
    profile_memo: str = ""  # 顧客詳細ページで手動編集するメモ（来店ごとのメモとは別）
    company: str = ""  # 顧客詳細ページで手動編集する会社名
    position: str = ""  # 顧客詳細ページで手動編集する役職（現在の値のみ保持）
