import calendar as cal_module
import re
from datetime import date, timedelta
from urllib.parse import unquote

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

import src.sheets_client as sc
from src.models import PLAN_KINDS, SHARED_EVENT_KINDS, Companion
from src.reminder import find_reminder_targets, find_upcoming_birthdays
from src.reports import build_period_summary, list_available_months, month_bounds
from src.search import find_visit_history

from .auth import login_required

_COMPANION_NAME_KEY = re.compile(r"^companion_name_(\d+)$")


def _parse_companions_from_form(form) -> list[Companion]:
    companions = []
    for key in form:
        match = _COMPANION_NAME_KEY.match(key)
        if not match:
            continue
        name = form[key].strip()
        if not name:
            continue
        index = match.group(1)
        has_contact = f"companion_contact_{index}" in form
        companions.append(Companion(name=name, has_contact=has_contact))
    return companions


def _build_row_from_form(form, is_own: bool) -> tuple[str, list[str]]:
    """入力フォーム／来店記録の修正フォーム共通：フォームの内容をシートの1行に変換する"""
    entry_date = form.get("date") or date.today().isoformat()
    names = form.get("names", "").strip()
    accompanied = "有" if "accompanied" in form else "無"
    accompany_note = form.get("accompany_note", "").strip()
    companions_str = sc.serialize_companions(_parse_companions_from_form(form))
    has_contact = "有" if "has_contact" in form else "無"
    last_contact_date = form.get("last_contact_date", "")
    birthday = form.get("birthday", "").strip()
    memo = form.get("memo", "").strip()
    sales_amount = form.get("sales_amount", "").strip()

    if is_own:
        row = [entry_date, names, accompanied, accompany_note, companions_str, has_contact, sales_amount, last_contact_date, birthday, memo]
    else:
        staff = form.get("staff_in_charge", "")
        new_or_existing = form.get("new_or_existing", "既存")
        row = [entry_date, staff, names, new_or_existing, accompanied, accompany_note, companions_str, has_contact, sales_amount, last_contact_date, birthday, memo]

    return names, row

bp = Blueprint("routes", __name__)


def _sid() -> str:
    return current_app.config["SPREADSHEET_ID"]


@bp.route("/")
@login_required
def reminders():
    master = sc.read_customer_master(_sid())
    today = date.today()

    targets = find_reminder_targets(master, today, current_app.config["DEFAULT_CONTACT_FREQUENCY_DAYS"])
    targets.sort(key=lambda r: r.last_contact_date or date.min)

    birthdays = find_upcoming_birthdays(master, today, current_app.config["BIRTHDAY_LOOKAHEAD_DAYS"])
    birthdays.sort(key=lambda r: r.birthday or "")

    return render_template("reminders.html", targets=targets, birthdays=birthdays, today=today)


@bp.route("/input", methods=["GET", "POST"])
@login_required
def input_form():
    if request.method == "POST":
        is_own = request.form.get("customer_type") == "own"
        names, row = _build_row_from_form(request.form, is_own)

        if not names:
            flash("名前を入力してください")
            return redirect(url_for("routes.input_form"))

        tab = sc.latest_period_tab(_sid(), is_own_customer=is_own)

        if tab is None:
            flash("入力先の期間タブがありません。先に期間タブを作成してください。")
            return redirect(url_for("routes.input_form"))

        sc.append_daily_entry_row(_sid(), tab, row)
        sc.refresh_customer_master(_sid())

        plan_id = request.form.get("plan_id", "").strip()
        if plan_id.isdigit():
            sc.mark_plan_recorded(_sid(), int(plan_id))

        flash(f"{names} さんを記録しました")
        return redirect(url_for("routes.input_form"))

    master = sc.read_customer_master(_sid())
    known_customers = [
        {
            "name": r.name,
            "has_contact": bool(r.has_contact),
            "is_own_customer": r.is_own_customer,
            "staff_in_charge": r.staff_in_charge or "",
        }
        for r in master.values()
    ]

    plans = [(row_number, p) for row_number, p in sc.list_plans(_sid()) if not p.recorded]

    plan_id = request.args.get("plan_id", type=int)
    prefill_name = ""
    prefill_accompanied = False
    if plan_id:
        plan = sc.get_plan(_sid(), plan_id)
        if plan:
            prefill_name = plan.name
            prefill_accompanied = plan.kind == "同伴"

    return render_template(
        "input.html",
        today=date.today().isoformat(),
        staff_names=sc.list_staff_names(_sid()),
        known_customers=known_customers,
        plans=plans,
        plan_id=plan_id,
        prefill_name=prefill_name,
        prefill_accompanied=prefill_accompanied,
    )


def _group_customers_by_own_and_staff(records, staff_order):
    """自客を1グループ、他客は係ごとに1グループずつに分け、各グループ内は最終来店日が新しい順に並べる"""

    def latest_visit(r):
        return max(r.visit_dates) if r.visit_dates else date.min

    groups = []
    own = sorted([r for r in records if r.is_own_customer], key=latest_visit, reverse=True)
    if own:
        groups.append({"key": "own", "label": "自客", "records": own})

    by_staff = {}
    for r in records:
        if r.is_own_customer:
            continue
        staff = r.staff_in_charge or "未設定"
        by_staff.setdefault(staff, []).append(r)

    ordered_staff_names = list(staff_order) + [s for s in by_staff if s not in staff_order]
    for staff in ordered_staff_names:
        recs = by_staff.get(staff)
        if not recs:
            continue
        groups.append({
            "key": f"staff-{staff}",
            "label": staff,
            "records": sorted(recs, key=latest_visit, reverse=True),
        })

    return groups


@bp.route("/customers")
@login_required
def customers():
    master = sc.read_customer_master(_sid())
    staff_order = sc.list_staff_names(_sid())
    groups = _group_customers_by_own_and_staff(master.values(), staff_order)
    return render_template("customers.html", groups=groups, total=len(master))


@bp.route("/customers/<customer_id>/frequency", methods=["POST"])
@login_required
def update_frequency(customer_id):
    raw = request.form.get("frequency_days", "").strip()
    days = int(raw) if raw.isdigit() else None
    sc.update_contact_frequency(_sid(), customer_id, days)
    flash("連絡頻度を更新しました")
    return redirect(url_for("routes.customers"))


def _find_record_by_id(master, customer_id):
    for record in master.values():
        if record.customer_id == customer_id:
            return record
    return None


@bp.route("/customers/<customer_id>")
@login_required
def customer_detail(customer_id):
    master = sc.read_customer_master(_sid())
    record = _find_record_by_id(master, customer_id)
    if record is None:
        flash("顧客が見つかりませんでした")
        return redirect(url_for("routes.customers"))
    return render_template("customer_detail.html", r=record)


@bp.route("/customers/<customer_id>/profile", methods=["POST"])
@login_required
def update_profile(customer_id):
    birthday = request.form.get("birthday", "").strip()
    profile_memo = request.form.get("profile_memo", "").strip()
    company = request.form.get("company", "").strip()
    position = request.form.get("position", "").strip()
    sc.update_customer_profile(
        _sid(), customer_id,
        birthday=birthday, profile_memo=profile_memo, company=company, position=position,
    )
    flash("プロフィールを更新しました")
    return redirect(url_for("routes.customer_detail", customer_id=customer_id))


@bp.route("/calendar")
@login_required
def calendar_view():
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month

    first_day = date(year, month, 1)
    days_in_month = cal_module.monthrange(year, month)[1]
    last_day = date(year, month, days_in_month)
    prev_ref = first_day - timedelta(days=1)
    next_ref = last_day + timedelta(days=1)

    service = sc.get_sheets_service()
    events = sc.list_events_in_range(_sid(), first_day, last_day, service=service)
    plans = sc.list_plans(_sid(), service=service)

    events_by_day = {}
    for e in events:
        d = max(e.start_date, first_day)
        end = min(e.end_date, last_day)
        while d <= end:
            events_by_day.setdefault(d, []).append({"category": "shared", "label": e.title or e.kind, "kind": e.kind})
            d += timedelta(days=1)
    for _row, p in plans:
        if first_day <= p.date <= last_day:
            events_by_day.setdefault(p.date, []).append({"category": "personal", "label": f"{p.kind}:{p.name}", "kind": p.kind})

    cal = cal_module.Calendar(firstweekday=6)  # 6=日曜始まり
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        cells = []
        for day_num in week:
            if day_num == 0:
                cells.append(None)
            else:
                d = date(year, month, day_num)
                cells.append({"date": d, "day": day_num, "events": events_by_day.get(d, []), "is_today": d == today})
        weeks.append(cells)

    selected = sc.parse_date(request.args.get("selected", ""))
    selected_events = events_by_day.get(selected, []) if selected else []

    return render_template(
        "calendar.html",
        year=year, month=month, weeks=weeks,
        prev_year=prev_ref.year, prev_month=prev_ref.month,
        next_year=next_ref.year, next_month=next_ref.month,
        selected=selected, selected_events=selected_events,
        shared_kinds=SHARED_EVENT_KINDS, today=today,
    )


@bp.route("/calendar/events", methods=["POST"])
@login_required
def add_event():
    kind = request.form.get("kind", "")
    start = sc.parse_date(request.form.get("start_date", ""))
    end = sc.parse_date(request.form.get("end_date", "")) or start
    title = request.form.get("title_custom", "").strip() or request.form.get("title_preset", "").strip() or request.form.get("title", "").strip()

    if kind not in SHARED_EVENT_KINDS or start is None:
        flash("入力内容を確認してください")
        return redirect(url_for("routes.calendar_view"))

    sc.add_event(_sid(), kind, start, end, title)
    flash(f"「{kind}」を追加しました")
    return redirect(url_for("routes.calendar_view", year=start.year, month=start.month, selected=start.isoformat()))


@bp.route("/calendar/plans", methods=["POST"])
@login_required
def add_plan():
    on_date = sc.parse_date(request.form.get("date", ""))
    kind = request.form.get("kind", "")
    name = request.form.get("name", "").strip()

    if on_date is None or kind not in PLAN_KINDS or not name:
        flash("入力内容を確認してください")
        return redirect(url_for("routes.calendar_view"))

    sc.add_plan(_sid(), on_date, kind, name)
    flash(f"{name} さんの{kind}予定を追加しました")
    return redirect(url_for("routes.calendar_view", year=on_date.year, month=on_date.month, selected=on_date.isoformat()))


@bp.route("/records")
@login_required
def records():
    service = sc.get_sheets_service()
    own = sc.read_daily_entries_with_location(_sid(), is_own_customer=True, service=service)
    other = sc.read_daily_entries_with_location(_sid(), is_own_customer=False, service=service)
    combined = own + other

    months = sorted({(entry.date.year, entry.date.month) for _tab, _row, entry in combined})

    grouped = {}
    for tab, row_number, entry in combined:
        key = f"{entry.date.year}-{entry.date.month:02d}"
        grouped.setdefault(key, []).append((tab, row_number, entry))
    for key in grouped:
        grouped[key].sort(key=lambda item: item[2].date, reverse=True)

    return render_template("records.html", months=months, grouped=grouped)


@bp.route("/records/<path:tab>/<int:row_number>/edit", methods=["GET", "POST"])
@login_required
def record_edit(tab, row_number):
    # VercelのWSGIアダプタはPATH_INFOをデコードせずに渡してくるため、
    # タブ名（日本語を含む）がパーセントエンコードされたまま届くことがある
    tab = unquote(tab)
    is_own = tab.startswith(sc.OWN_TAB_PREFIX)

    if request.method == "POST":
        target_is_own = request.form.get("customer_type", "own" if is_own else "other") == "own"
        names, row = _build_row_from_form(request.form, target_is_own)
        if not names:
            flash("名前を入力してください")
            return redirect(url_for("routes.record_edit", tab=tab, row_number=row_number))

        if target_is_own == is_own:
            sc.update_daily_entry_row(_sid(), tab, row_number, row)
        else:
            # 自客⇔他客の変更は列構成が違うタブへの移動になるため、移動先に追加してから元の行を削除する
            sc.append_daily_entry_row(_sid(), sc.sibling_period_tab(tab), row)
            sc.delete_daily_entry_row(_sid(), tab, row_number)
        sc.refresh_customer_master(_sid())
        flash("来店記録を修正しました")
        return redirect(url_for("routes.records"))

    raw_row = sc.get_raw_row(_sid(), tab, row_number)
    entry = sc.parse_entry_row(raw_row, is_own)
    if entry is None:
        flash("記録が見つかりませんでした")
        return redirect(url_for("routes.records"))

    return render_template(
        "record_edit.html",
        tab=tab,
        row_number=row_number,
        entry=entry,
        staff_names=sc.list_staff_names(_sid()),
    )


@bp.route("/records/<path:tab>/<int:row_number>/delete", methods=["POST"])
@login_required
def record_delete(tab, row_number):
    tab = unquote(tab)
    sc.delete_daily_entry_row(_sid(), tab, row_number)
    sc.refresh_customer_master(_sid())
    flash("来店記録を削除しました")
    return redirect(url_for("routes.records"))


@bp.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    results = []
    if query:
        master = sc.read_customer_master(_sid())
        matched_names = sorted(name for name in master if query in name)

        own_entries = sc.read_daily_entries(_sid(), is_own_customer=True)
        other_entries = sc.read_daily_entries(_sid(), is_own_customer=False)
        entries = own_entries + other_entries

        for name in matched_names:
            history = find_visit_history(entries, name)
            results.append({"name": name, "record": master[name], "history": history})

    return render_template("search.html", query=query, results=results)


@bp.route("/summary")
@login_required
def summary():
    service = sc.get_sheets_service()
    period = sc.current_period(_sid(), service=service)

    own_entries = sc.read_daily_entries(_sid(), is_own_customer=True, service=service)
    other_entries = sc.read_daily_entries(_sid(), is_own_customer=False, service=service)
    entries = own_entries + other_entries

    if period is None or period["start_date"] is None or period["end_date"] is None:
        flash("期間が設定されていません。設定画面で期間を作成してください。")
        return render_template("summary.html", summary=None, trend=[], past_months=[], past_months_json={})

    result = build_period_summary(entries, period["start_date"], period["end_date"])

    today = date.today()
    available_months = list_available_months(entries)
    month_summaries = {(y, m): build_period_summary(entries, *month_bounds(y, m)) for (y, m) in available_months}

    max_sales = max([s.total_sales for s in month_summaries.values()] + [0])
    max_count = max([s.accompanied_count for s in month_summaries.values()] + [0])

    trend = []
    for (y, m) in available_months:
        s = month_summaries[(y, m)]
        trend.append({
            "label": f"{m}月",
            "sales": s.total_sales,
            "sales_pct": round(s.total_sales / max_sales * 100) if max_sales else 0,
            "count": s.accompanied_count,
            "count_pct": round(s.accompanied_count / max_count * 100) if max_count else 0,
            "is_current": (y, m) == (today.year, today.month),
        })

    # 「過去の実績」は今の期間と暦月が重ならない、完全に終わった月だけを対象にする
    # （締め日が月末とは限らないため、今の期間の一部が前月にまたがることがある）
    past_months = [(y, m) for (y, m) in available_months if month_bounds(y, m)[1] < period["start_date"]]
    past_months_json = {}
    for (y, m) in past_months:
        s = month_summaries[(y, m)]
        past_months_json[f"{y}-{m:02d}"] = {
            "label": f"{y}年{m}月",
            "sales": s.total_sales,
            "count": s.accompanied_count,
            "visits": [
                {
                    "date": v.date.isoformat(),
                    "names": "・".join(v.customer_names),
                    "note": f"係: {v.staff_in_charge}" if v.staff_in_charge else "自分が同伴",
                    "detail": v.accompany_note,
                }
                for v in s.accompanied_visits
            ],
        }

    return render_template(
        "summary.html",
        summary=result,
        trend=trend,
        past_months=past_months,
        past_months_json=past_months_json,
    )


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        theme = request.form.get("theme")
        if theme in ("dark-gold", "light-ink"):
            session["theme"] = theme
            flash("テーマを変更しました")
        return redirect(url_for("routes.settings"))

    period = sc.current_period(_sid())
    staff_names = sc.list_staff_names(_sid())
    return render_template(
        "settings.html",
        current_theme=session.get("theme", "dark-gold"),
        period=period,
        staff_names=staff_names,
    )


@bp.route("/settings/staff/add", methods=["POST"])
@login_required
def add_staff():
    name = request.form.get("name", "").strip()
    if not name:
        flash("係名を入力してください")
        return redirect(url_for("routes.settings"))
    sc.add_staff_name(_sid(), name)
    flash(f"「{name}」を追加しました")
    return redirect(url_for("routes.settings"))


@bp.route("/settings/staff/rename", methods=["POST"])
@login_required
def rename_staff():
    old_name = request.form.get("old_name", "").strip()
    new_name = request.form.get("new_name", "").strip()
    if not old_name or not new_name:
        flash("係名を入力してください")
        return redirect(url_for("routes.settings"))
    sc.rename_staff_name(_sid(), old_name, new_name)
    flash(f"「{old_name}」を「{new_name}」に変更しました（過去の記録もすべて置き換えました）")
    return redirect(url_for("routes.settings"))


@bp.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    sid = _sid()
    initial_password = current_app.config["INITIAL_APP_PASSWORD"]

    if not sc.verify_password(sid, current_password, initial_password):
        flash("現在のパスワードが違います")
        return redirect(url_for("routes.settings"))
    if not new_password or new_password != confirm_password:
        flash("新しいパスワードが一致しません")
        return redirect(url_for("routes.settings"))

    sc.change_password(sid, new_password)
    flash("パスワードを変更しました")
    return redirect(url_for("routes.settings"))


@bp.route("/settings/update-period", methods=["POST"])
@login_required
def update_period():
    start_date = sc.parse_date(request.form.get("start_date", ""))
    end_date = sc.parse_date(request.form.get("end_date", ""))

    if start_date is None or end_date is None:
        flash("開始日・締め日を正しく入力してください")
        return redirect(url_for("routes.settings"))
    if end_date < start_date:
        flash("締め日は開始日より後の日付にしてください")
        return redirect(url_for("routes.settings"))

    sc.update_period_tab(_sid(), start_date=start_date, end_date=end_date)
    flash("今の期間を変更しました")
    return redirect(url_for("routes.settings"))


@bp.route("/settings/new-period", methods=["POST"])
@login_required
def new_period():
    start_date = sc.parse_date(request.form.get("start_date", ""))
    end_date = sc.parse_date(request.form.get("end_date", ""))

    if start_date is None or end_date is None:
        flash("開始日・締め日を正しく入力してください")
        return redirect(url_for("routes.settings"))
    if end_date < start_date:
        flash("締め日は開始日より後の日付にしてください")
        return redirect(url_for("routes.settings"))

    label = sc.period_label_from_dates(start_date, end_date)
    sc.create_period_tab(_sid(), label=label, start_date=start_date, end_date=end_date)
    flash(f"新しい期間「{label}」を作成しました")
    return redirect(url_for("routes.settings"))
