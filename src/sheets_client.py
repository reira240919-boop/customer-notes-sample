"""Googleスプレッドシートとの連携

入力タブは「期間（締め日が月末とは限らない営業サイクル）＝1タブ」で管理する。
タブ名は "自客_<ラベル>" / "他客_<ラベル>" の形式（例: 自客_8/1-8/25）。
各タブのレイアウトは以下の3行構成:
  1行目: 開始日・締め日の入力欄
  2行目: 列見出し
  3行目以降: データ
顧客マスタ（集約先）は全期間を通じて1枚のシートのまま更新し続ける。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from werkzeug.security import check_password_hash, generate_password_hash

from . import sample_sheets
from .models import CalendarEvent, Companion, CustomerRecord, DailyEntry, Plan

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
# ローカルでは既定のパスを使い、Render等ではSecret Fileの実際の配置パスを
# 環境変数で上書きできるようにする（credentials/はGit管理外のため）
CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json")
# Vercelはファイル配置の仕組みがないため、鍵の中身をそのまま環境変数に入れる方式にも対応する
CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

OWN_TAB_PREFIX = "自客_"
OTHER_TAB_PREFIX = "他客_"
MASTER_TAB = "顧客マスタ"
STAFF_TAB = "係"
AUTH_TAB = "ログイン設定"

OWN_CUSTOMER_HEADERS = ["日付", "名前", "同伴", "同伴メモ", "同伴者", "連絡先", "売上", "最終連絡日", "誕生日", "メモ"]
OTHER_CUSTOMER_HEADERS = ["日付", "係", "名前", "新規既存", "同伴", "同伴メモ", "同伴者", "連絡先", "売上", "最終連絡日", "誕生日", "メモ"]
MASTER_HEADERS = [
    "顧客ID", "名前", "自客他客", "係", "来店履歴", "新規既存",
    "連絡先有無", "最終連絡日", "連絡頻度日数", "誕生日", "メモ", "同伴履歴", "紹介元（幹）",
    "プロフィールメモ", "会社名", "役職",
]
STAFF_HEADERS = ["係名"]
AUTH_HEADERS = ["パスワードハッシュ"]
EVENTS_TAB = "共通予定"
EVENTS_HEADERS = ["種別", "開始日", "終了日", "タイトル"]
PLANS_TAB = "個人予定"
PLANS_HEADERS = ["日付", "種別", "名前", "記録済み"]

PERIOD_INFO_ROW = 1  # 開始日・締め日
HEADER_ROW = 2  # 列見出し
DATA_START_ROW = 3  # データ開始行

DEFAULT_STAFF_NAMES = [
    "ママ", "スタッフA", "スタッフB", "スタッフC", "スタッフD", "社長", "お店",
]

VALIDATION_ROW_COUNT = 1000  # データ開始行から何行分プルダウン等を効かせておくか

_service_singleton = None  # プロセス内で使い回し、リクエストのたびに接続を作り直さない


def get_sheets_service():
    global _service_singleton
    if _service_singleton is None and sample_sheets.is_sample(os.environ.get("SPREADSHEET_ID")):
        # 見本：本物のスプレッドシートにはつながず、作り物のデータで動かす
        sample_service = sample_sheets.SampleSheetsService()
        sample_sheets.seed(sample_service, os.environ.get("APP_PASSWORD", "sample"))
        _service_singleton = sample_service  # 準備が全部終わってから使い始める
    if _service_singleton is None:
        if CREDENTIALS_JSON:
            info = json.loads(CREDENTIALS_JSON)
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        else:
            creds = service_account.Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        _service_singleton = build("sheets", "v4", credentials=creds)
    return _service_singleton


_ensured_tabs: set[str] = set()  # 一度存在確認・初期化できたタブは、プロセス内では再確認しない


def _already_ensured(tab_name: str) -> bool:
    return tab_name in _ensured_tabs


def _mark_ensured(tab_name: str) -> None:
    _ensured_tabs.add(tab_name)


def _get_sheet_meta(spreadsheet_id: str, service) -> dict:
    """タブの名前・IDだけを取得する（条件付き書式や保護範囲などは使わないため、
    fieldsで絞り込んで不要な巨大レスポンスを避ける）"""
    return service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)"
    ).execute()


def _sheet_id_from_meta(meta: dict, tab_name: str) -> int:
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]
    raise ValueError(f"タブが見つかりません: {tab_name}")


def _get_sheet_id(spreadsheet_id: str, tab_name: str, service) -> int:
    meta = _get_sheet_meta(spreadsheet_id, service)
    return _sheet_id_from_meta(meta, tab_name)


def list_period_tabs(spreadsheet_id: str, prefix: str, service=None) -> list[str]:
    """指定したプレフィックス（自客_ / 他客_）に一致するタブ名を、開始日の昇順で返す"""
    service = service or get_sheets_service()
    meta = _get_sheet_meta(spreadsheet_id, service)
    tab_names = [s["properties"]["title"] for s in meta["sheets"] if s["properties"]["title"].startswith(prefix)]
    if not tab_names:
        return []

    # タブ数だけ1件ずつ開始日を取得すると通信回数が増えるため、batchGetでまとめて取得する
    ranges = [f"{name}!B{PERIOD_INFO_ROW}" for name in tab_names]
    response = service.spreadsheets().values().batchGet(spreadsheetId=spreadsheet_id, ranges=ranges).execute()
    value_ranges = response.get("valueRanges", [])

    start_dates = {}
    for name, value_range in zip(tab_names, value_ranges):
        values = value_range.get("values", [])
        parsed = _parse_date(values[0][0]) if values and values[0] else None
        start_dates[name] = parsed or date.min

    return sorted(tab_names, key=lambda name: start_dates[name])


def current_period(spreadsheet_id: str, service=None) -> dict | None:
    """一番新しい期間タブの開始日・締め日・ラベルを返す（期間タブが無ければNone）"""
    service = service or get_sheets_service()
    tabs = list_period_tabs(spreadsheet_id, OWN_TAB_PREFIX, service=service)
    if not tabs:
        return None
    latest_tab = tabs[-1]
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{latest_tab}!A{PERIOD_INFO_ROW}:D{PERIOD_INFO_ROW}").execute()
    values = result.get("values", [[]])[0]
    values = values + [""] * (4 - len(values))
    start_date = _parse_date(values[1])
    end_date = _parse_date(values[3])
    return {
        "label": latest_tab[len(OWN_TAB_PREFIX):],
        "start_date": start_date,
        "end_date": end_date,
    }


def period_label_from_dates(start_date: date, end_date: date) -> str:
    return f"{start_date.month}/{start_date.day}-{end_date.month}/{end_date.day}"


def update_period_tab(spreadsheet_id: str, start_date: date, end_date: date, service=None) -> None:
    """一番新しい期間タブの開始日・締め日（とタブ名のラベル）を書き換える。
    新しいタブを作らず、既存のデータはそのままにして日付だけ直す"""
    service = service or get_sheets_service()
    own_tabs = list_period_tabs(spreadsheet_id, OWN_TAB_PREFIX, service=service)
    other_tabs = list_period_tabs(spreadsheet_id, OTHER_TAB_PREFIX, service=service)
    if not own_tabs or not other_tabs:
        raise ValueError("期間タブが見つかりません")

    old_own_title = own_tabs[-1]
    old_other_title = other_tabs[-1]

    new_label = period_label_from_dates(start_date, end_date)
    new_own_title = f"{OWN_TAB_PREFIX}{new_label}"
    new_other_title = f"{OTHER_TAB_PREFIX}{new_label}"

    meta = _get_sheet_meta(spreadsheet_id, service)
    own_sheet_id = _sheet_id_from_meta(meta, old_own_title)
    other_sheet_id = _sheet_id_from_meta(meta, old_other_title)

    rename_requests = []
    if new_own_title != old_own_title:
        rename_requests.append({"updateSheetProperties": {
            "properties": {"sheetId": own_sheet_id, "title": new_own_title},
            "fields": "title",
        }})
    if new_other_title != old_other_title:
        rename_requests.append({"updateSheetProperties": {
            "properties": {"sheetId": other_sheet_id, "title": new_other_title},
            "fields": "title",
        }})
    if rename_requests:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": rename_requests}).execute()

    period_row = ["開始日", start_date.isoformat(), "締め日", end_date.isoformat()]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{new_own_title}!A{PERIOD_INFO_ROW}",
        valueInputOption="RAW", body={"values": [period_row]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{new_other_title}!A{PERIOD_INFO_ROW}",
        valueInputOption="RAW", body={"values": [period_row]},
    ).execute()


# ---------------------------------------------------------------------------
# 期間タブの新規作成
# ---------------------------------------------------------------------------

def _list_validation_request(sheet_id: int, column_index: int, choices: list[str], strict: bool = True) -> dict:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": DATA_START_ROW - 1,
                "endRowIndex": DATA_START_ROW - 1 + VALIDATION_ROW_COUNT,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": choice} for choice in choices],
                },
                "showCustomUi": True,
                "strict": strict,
            },
        }
    }


def _date_validation_request(sheet_id: int, column_index: int) -> dict:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": DATA_START_ROW - 1,
                "endRowIndex": DATA_START_ROW - 1 + VALIDATION_ROW_COUNT,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            },
            "rule": {
                "condition": {"type": "DATE_IS_VALID"},
                "showCustomUi": True,
                "strict": True,
            },
        }
    }


def _header_format_request(sheet_id: int, row_index: int, num_columns: int) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_index,
                "endRowIndex": row_index + 1,
                "startColumnIndex": 0,
                "endColumnIndex": num_columns,
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER",
                }
            },
            "fields": "userEnteredFormat(textFormat,horizontalAlignment)",
        }
    }


def _wide_column_request(sheet_id: int, column_index: int, pixel_size: int = 320) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": column_index,
                "endIndex": column_index + 1,
            },
            "properties": {"pixelSize": pixel_size},
            "fields": "pixelSize",
        }
    }


def _input_validation_requests(sheet_id: int, is_own_customer: bool, staff_names: list[str]) -> list[dict]:
    yes_no = ["有", "無"]
    new_or_existing = ["新規", "既存"]

    if is_own_customer:
        # 日付(0), 同伴有無(2), 連絡先有無・幹(5), 最終連絡日(7), メモ(9)
        return [
            _date_validation_request(sheet_id, 0),
            _list_validation_request(sheet_id, 2, yes_no),
            _list_validation_request(sheet_id, 5, yes_no),
            _date_validation_request(sheet_id, 7),
            _wide_column_request(sheet_id, 9),
        ]
    # 他客: 日付(0), 係(1), 新規既存(3), 同伴(4), 連絡先(7), 最終連絡日(9), メモ(11)
    return [
        _date_validation_request(sheet_id, 0),
        _list_validation_request(sheet_id, 1, staff_names, strict=False),
        _list_validation_request(sheet_id, 3, new_or_existing),
        _list_validation_request(sheet_id, 4, yes_no),
        _list_validation_request(sheet_id, 7, yes_no),
        _date_validation_request(sheet_id, 9),
        _wide_column_request(sheet_id, 11),
    ]


def create_period_tab(
    spreadsheet_id: str,
    label: str,
    start_date: date,
    end_date: date | None,
    service=None,
) -> None:
    """新しい期間の自客/他客入力タブを作成する（例: label="8/1-8/25"）"""
    service = service or get_sheets_service()
    staff_names = list_staff_names(spreadsheet_id, service=service)

    own_title = f"{OWN_TAB_PREFIX}{label}"
    other_title = f"{OTHER_TAB_PREFIX}{label}"

    add_requests = [
        {"addSheet": {"properties": {"title": own_title}}},
        {"addSheet": {"properties": {"title": other_title}}},
    ]
    response = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": add_requests}).execute()
    own_sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]
    other_sheet_id = response["replies"][1]["addSheet"]["properties"]["sheetId"]

    period_row = ["開始日", start_date.isoformat(), "締め日", end_date.isoformat() if end_date else ""]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{own_title}!A{PERIOD_INFO_ROW}",
        valueInputOption="RAW", body={"values": [period_row]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{other_title}!A{PERIOD_INFO_ROW}",
        valueInputOption="RAW", body={"values": [period_row]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{own_title}!A{HEADER_ROW}",
        valueInputOption="RAW", body={"values": [OWN_CUSTOMER_HEADERS]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{other_title}!A{HEADER_ROW}",
        valueInputOption="RAW", body={"values": [OTHER_CUSTOMER_HEADERS]},
    ).execute()

    format_requests = [
        _header_format_request(own_sheet_id, PERIOD_INFO_ROW - 1, 4),
        _header_format_request(own_sheet_id, HEADER_ROW - 1, len(OWN_CUSTOMER_HEADERS)),
        {"updateSheetProperties": {
            "properties": {"sheetId": own_sheet_id, "gridProperties": {"frozenRowCount": HEADER_ROW}},
            "fields": "gridProperties.frozenRowCount",
        }},
        _header_format_request(other_sheet_id, PERIOD_INFO_ROW - 1, 4),
        _header_format_request(other_sheet_id, HEADER_ROW - 1, len(OTHER_CUSTOMER_HEADERS)),
        {"updateSheetProperties": {
            "properties": {"sheetId": other_sheet_id, "gridProperties": {"frozenRowCount": HEADER_ROW}},
            "fields": "gridProperties.frozenRowCount",
        }},
        *_input_validation_requests(own_sheet_id, is_own_customer=True, staff_names=staff_names),
        *_input_validation_requests(other_sheet_id, is_own_customer=False, staff_names=staff_names),
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": format_requests}).execute()


def ensure_master_tab(spreadsheet_id: str, service=None) -> None:
    """顧客マスタタブのヘッダーを常に最新のMASTER_HEADERSに合わせ、太字＋中央揃え＋1行固定にする"""
    service = service or get_sheets_service()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{MASTER_TAB}!A1",
        valueInputOption="RAW", body={"values": [MASTER_HEADERS]},
    ).execute()

    sheet_id = _get_sheet_id(spreadsheet_id, MASTER_TAB, service)
    requests = [
        _header_format_request(sheet_id, 0, len(MASTER_HEADERS)),
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


# ---------------------------------------------------------------------------
# ログインパスワードの管理
# ---------------------------------------------------------------------------

def ensure_auth_tab(spreadsheet_id: str, initial_password: str, service=None) -> None:
    """ログイン設定タブが無ければ作成し、初期パスワードのハッシュで初期化する（プロセス内では一度だけ確認すればよい）"""
    if _already_ensured(AUTH_TAB):
        return
    service = service or get_sheets_service()
    meta = _get_sheet_meta(spreadsheet_id, service)
    existing_titles = [s["properties"]["title"] for s in meta["sheets"]]
    tab_is_new = AUTH_TAB not in existing_titles
    if tab_is_new:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [{"addSheet": {"properties": {"title": AUTH_TAB}}}]}
        ).execute()

    existing = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{AUTH_TAB}!1:1").execute()
    headers_are_new = not existing.get("values")
    if headers_are_new:
        rows = [AUTH_HEADERS, [generate_password_hash(initial_password, method="pbkdf2:sha256")]]
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{AUTH_TAB}!A1",
            valueInputOption="RAW", body={"values": rows},
        ).execute()

    # 書式(太字・固定行)はタブを新しく作った時だけ整えればよく、既存タブでは
    # 前回までに設定済みのため、毎回のプロセス起動のたびにやり直す必要はない
    if tab_is_new or headers_are_new:
        sheet_id = (
            _sheet_id_from_meta(meta, AUTH_TAB)
            if not tab_is_new
            else _get_sheet_id(spreadsheet_id, AUTH_TAB, service)
        )
        requests = [
            _header_format_request(sheet_id, 0, len(AUTH_HEADERS)),
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    _mark_ensured(AUTH_TAB)


def verify_password(spreadsheet_id: str, password: str, initial_password: str, service=None) -> bool:
    service = service or get_sheets_service()
    ensure_auth_tab(spreadsheet_id, initial_password, service=service)
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{AUTH_TAB}!A2").execute()
    values = result.get("values", [])
    stored_hash = values[0][0] if values and values[0] else ""
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, password)


def change_password(spreadsheet_id: str, new_password: str, service=None) -> None:
    service = service or get_sheets_service()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{AUTH_TAB}!A2",
        valueInputOption="RAW", body={"values": [[generate_password_hash(new_password, method="pbkdf2:sha256")]]},
    ).execute()


# ---------------------------------------------------------------------------
# 係一覧の管理
# ---------------------------------------------------------------------------

def ensure_staff_tab(spreadsheet_id: str, service=None) -> None:
    """係タブが無ければ作成し、既定の係名一覧で初期化する（プロセス内では一度だけ確認すればよい）"""
    if _already_ensured(STAFF_TAB):
        return
    service = service or get_sheets_service()
    meta = _get_sheet_meta(spreadsheet_id, service)
    existing_titles = [s["properties"]["title"] for s in meta["sheets"]]
    tab_is_new = STAFF_TAB not in existing_titles
    if tab_is_new:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [{"addSheet": {"properties": {"title": STAFF_TAB}}}]}
        ).execute()

    existing = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{STAFF_TAB}!1:1").execute()
    headers_are_new = not existing.get("values")
    if headers_are_new:
        rows = [STAFF_HEADERS] + [[name] for name in DEFAULT_STAFF_NAMES]
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{STAFF_TAB}!A1",
            valueInputOption="RAW", body={"values": rows},
        ).execute()

    if tab_is_new or headers_are_new:
        sheet_id = (
            _sheet_id_from_meta(meta, STAFF_TAB)
            if not tab_is_new
            else _get_sheet_id(spreadsheet_id, STAFF_TAB, service)
        )
        requests = [
            _header_format_request(sheet_id, 0, len(STAFF_HEADERS)),
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    _mark_ensured(STAFF_TAB)


def list_staff_names(spreadsheet_id: str, service=None) -> list[str]:
    service = service or get_sheets_service()
    ensure_staff_tab(spreadsheet_id, service=service)
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{STAFF_TAB}!A2:A").execute()
    return [row[0] for row in result.get("values", []) if row and row[0].strip()]


def add_staff_name(spreadsheet_id: str, name: str, service=None) -> None:
    service = service or get_sheets_service()
    name = name.strip()
    if not name:
        return
    if name in list_staff_names(spreadsheet_id, service=service):
        return
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=f"{STAFF_TAB}!A2",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [[name]]},
    ).execute()


def rename_staff_name(spreadsheet_id: str, old_name: str, new_name: str, service=None) -> None:
    """係名を変更し、過去の他客入力タブに残っている係名もすべて新しい名前に置き換える"""
    service = service or get_sheets_service()
    new_name = new_name.strip()
    if not new_name or old_name == new_name:
        return

    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{STAFF_TAB}!A2:A").execute()
    rows = result.get("values", [])
    updated_rows = [[new_name if (row and row[0] == old_name) else (row[0] if row else "")] for row in rows]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{STAFF_TAB}!A2",
        valueInputOption="RAW", body={"values": updated_rows},
    ).execute()

    staff_column_index = OTHER_CUSTOMER_HEADERS.index("係")
    column_letter = chr(ord("A") + staff_column_index)
    for tab in list_period_tabs(spreadsheet_id, OTHER_TAB_PREFIX, service=service):
        col_result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"{tab}!{column_letter}{DATA_START_ROW}:{column_letter}"
        ).execute()
        col_rows = col_result.get("values", [])
        if not col_rows:
            continue
        changed = False
        new_col = []
        for row in col_rows:
            value = row[0] if row else ""
            if value == old_name:
                value = new_name
                changed = True
            new_col.append([value])
        if changed:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id, range=f"{tab}!{column_letter}{DATA_START_ROW}",
                valueInputOption="RAW", body={"values": new_col},
            ).execute()


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------

def _parse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_date(text: str) -> date | None:
    """HTMLのdate inputなど、"YYYY-MM-DD"文字列をdateに変換する（公開用）"""
    return _parse_date(text)


def _parse_bool(text: str) -> bool | None:
    text = (text or "").strip()
    if text == "有":
        return True
    if text == "無":
        return False
    return None


def serialize_companions(companions: list[Companion]) -> str:
    """[Companion("鈴木", True), Companion("佐藤", False)] -> "鈴木:有,佐藤:無" """
    return ",".join(f"{c.name}:{_bool_to_text(c.has_contact) or '無'}" for c in companions)


def _parse_companions(text: str) -> list[Companion]:
    companions = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            name, flag = chunk.split(":", 1)
        else:
            name, flag = chunk, ""
        name = name.strip()
        if name:
            companions.append(Companion(name=name, has_contact=_parse_bool(flag)))
    return companions


def _parse_int(text: str) -> int | None:
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def _row_to_entry(row: list[str], is_own_customer: bool) -> DailyEntry | None:
    if is_own_customer:
        row = row + [""] * (len(OWN_CUSTOMER_HEADERS) - len(row))
        (date_str, names_str, accompanied, accompany_note, companions_str,
         has_contact, sales_str, last_contact, birthday, memo) = row[:10]
        staff_in_charge = None
        new_or_existing = ""
    else:
        row = row + [""] * (len(OTHER_CUSTOMER_HEADERS) - len(row))
        (date_str, staff_in_charge, names_str, new_or_existing, accompanied, accompany_note,
         companions_str, has_contact, sales_str, last_contact, birthday, memo) = row[:12]

    entry_date = _parse_date(date_str)
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    if entry_date is None or not names:
        return None

    return DailyEntry(
        date=entry_date,
        customer_names=names,
        is_own_customer=is_own_customer,
        staff_in_charge=staff_in_charge or None,
        new_or_existing=new_or_existing.strip() or ("" if is_own_customer else "既存"),
        has_contact=_parse_bool(has_contact),
        last_contact_date=_parse_date(last_contact),
        birthday=birthday.strip() or None,
        memo=memo.strip(),
        accompanied=_parse_bool(accompanied) or False,
        accompany_note=accompany_note.strip(),
        companions=_parse_companions(companions_str),
        sales_amount=_parse_int(sales_str),
    )


def read_daily_entries(spreadsheet_id: str, is_own_customer: bool, service=None) -> list[DailyEntry]:
    """自客_* / 他客_* の期間タブすべてを開始日順に読み込み、日次入力を1つのリストにまとめる"""
    service = service or get_sheets_service()
    return [entry for _tab, _row_number, entry in read_daily_entries_with_location(spreadsheet_id, is_own_customer, service=service)]


def read_daily_entries_with_location(
    spreadsheet_id: str, is_own_customer: bool, service=None
) -> list[tuple[str, int, DailyEntry]]:
    """(タブ名, シート上の行番号, DailyEntry) のリストを返す（来店記録の修正で該当行を特定するため）"""
    service = service or get_sheets_service()
    prefix = OWN_TAB_PREFIX if is_own_customer else OTHER_TAB_PREFIX
    tabs = list_period_tabs(spreadsheet_id, prefix, service)
    if not tabs:
        return []

    # タブ数だけ1件ずつ読むと通信回数が増えるため、batchGetでまとめて取得する
    ranges = [f"{tab}!A{DATA_START_ROW}:Z" for tab in tabs]
    response = service.spreadsheets().values().batchGet(spreadsheetId=spreadsheet_id, ranges=ranges).execute()
    value_ranges = response.get("valueRanges", [])

    results: list[tuple[str, int, DailyEntry]] = []
    for tab, value_range in zip(tabs, value_ranges):
        rows = value_range.get("values", [])
        for offset, row in enumerate(rows):
            if not any(cell.strip() for cell in row):
                continue
            entry = _row_to_entry(row, is_own_customer)
            if entry is not None:
                results.append((tab, DATA_START_ROW + offset, entry))
    return results


def parse_entry_row(row: list[str], is_own_customer: bool) -> DailyEntry | None:
    """来店記録の編集フォームの下書き表示用に、シートの1行をDailyEntryへ変換する（公開用）"""
    return _row_to_entry(row, is_own_customer)


def get_raw_row(spreadsheet_id: str, tab: str, row_number: int, service=None) -> list[str]:
    service = service or get_sheets_service()
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{tab}!A{row_number}:Z{row_number}").execute()
    values = result.get("values", [[]])
    return values[0] if values else []


def update_daily_entry_row(spreadsheet_id: str, tab: str, row_number: int, row: list[str], service=None) -> None:
    """来店記録の1行を丸ごと上書きする（打ち間違えの修正用）"""
    service = service or get_sheets_service()
    end_column_letter = chr(ord("A") + len(row) - 1)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A{row_number}:{end_column_letter}{row_number}",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()


def sibling_period_tab(tab: str) -> str:
    """自客⇔他客タブの間で、同じ期間ラベルのもう片方のタブ名を返す（記録の自客/他客の変更用）"""
    if tab.startswith(OWN_TAB_PREFIX):
        return OTHER_TAB_PREFIX + tab[len(OWN_TAB_PREFIX):]
    return OWN_TAB_PREFIX + tab[len(OTHER_TAB_PREFIX):]


def delete_daily_entry_row(spreadsheet_id: str, tab: str, row_number: int, service=None) -> None:
    """来店記録を1件削除する（二重登録・入力ミスの削除用）"""
    service = service or get_sheets_service()
    sheet_id = _get_sheet_id(spreadsheet_id, tab, service)
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"deleteDimension": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": row_number - 1, "endIndex": row_number}
        }}]},
    ).execute()


def _bool_to_text(value: bool | None) -> str:
    if value is True:
        return "有"
    if value is False:
        return "無"
    return ""


def _record_to_row(record: CustomerRecord) -> list[str]:
    return [
        record.customer_id,
        record.name,
        "自客" if record.is_own_customer else "他客",
        record.staff_in_charge or "",
        ",".join(d.isoformat() for d in record.visit_dates),
        record.new_or_existing,
        _bool_to_text(record.has_contact),
        record.last_contact_date.isoformat() if record.last_contact_date else "",
        str(record.contact_frequency_days) if record.contact_frequency_days is not None else "",
        record.birthday or "",
        record.memo,
        ",".join(d.isoformat() for d in record.accompanied_dates),
        record.introduced_by or "",
        record.profile_memo,
        record.company,
        record.position,
    ]


def write_customer_master(spreadsheet_id: str, master: dict[str, CustomerRecord], service=None) -> None:
    """顧客マスタタブを全件洗い替えで書き込む（ヘッダー行は常に最新の列構成に揃える）"""
    service = service or get_sheets_service()
    ensure_master_tab(spreadsheet_id, service=service)
    records = sorted(master.values(), key=lambda r: r.customer_id)
    rows = [_record_to_row(r) for r in records]

    service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"{MASTER_TAB}!A2:Z").execute()
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{MASTER_TAB}!A2",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()


def read_customer_master(spreadsheet_id: str, service=None) -> dict[str, CustomerRecord]:
    """既存の顧客マスタタブを読み込んでメモリ上の辞書に復元する（連絡頻度の個別設定などを引き継ぐため）"""
    service = service or get_sheets_service()
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{MASTER_TAB}!A2:Z").execute()
    rows = result.get("values", [])

    master: dict[str, CustomerRecord] = {}
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        row = row + [""] * (len(MASTER_HEADERS) - len(row))
        (customer_id, name, own_other, staff, visits, new_or_existing,
         has_contact, last_contact, frequency, birthday, memo, accompanied, introduced_by,
         profile_memo, company, position) = row[:16]

        master[name] = CustomerRecord(
            customer_id=customer_id,
            name=name,
            is_own_customer=(own_other == "自客"),
            staff_in_charge=staff or None,
            visit_dates=[_parse_date(d) for d in visits.split(",") if d.strip()],
            new_or_existing=new_or_existing or "既存",
            has_contact=_parse_bool(has_contact),
            last_contact_date=_parse_date(last_contact),
            contact_frequency_days=int(frequency) if frequency.strip().isdigit() else None,
            birthday=birthday or None,
            memo=memo,
            accompanied_dates=[_parse_date(d) for d in accompanied.split(",") if d.strip()],
            introduced_by=introduced_by or None,
            profile_memo=profile_memo,
            company=company,
            position=position,
        )
    return master


def latest_period_tab(spreadsheet_id: str, is_own_customer: bool, service=None) -> str | None:
    """開始日が一番新しい期間タブ名を返す（Webの入力フォームが書き込む先）"""
    service = service or get_sheets_service()
    prefix = OWN_TAB_PREFIX if is_own_customer else OTHER_TAB_PREFIX
    tabs = list_period_tabs(spreadsheet_id, prefix, service)
    return tabs[-1] if tabs else None


def append_daily_entry_row(spreadsheet_id: str, tab_name: str, row: list[str], service=None) -> None:
    """指定した期間タブの末尾（データ範囲内）に1行追記する"""
    service = service or get_sheets_service()
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A{DATA_START_ROW}:Z",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def refresh_customer_master(spreadsheet_id: str, service=None) -> dict[str, CustomerRecord]:
    """全期間タブの日次入力から顧客マスタを再集計して書き込む。
    連絡頻度日数（連絡頻度の個別設定）は日次入力に存在しないため、
    書き込み前の顧客マスタから名前をキーに引き継ぐ。
    """
    from .aggregate import aggregate_daily_entries  # 循環importを避けるため遅延import

    service = service or get_sheets_service()
    own_entries = read_daily_entries(spreadsheet_id, is_own_customer=True, service=service)
    other_entries = read_daily_entries(spreadsheet_id, is_own_customer=False, service=service)
    new_master = aggregate_daily_entries(own_entries + other_entries)

    old_master = read_customer_master(spreadsheet_id, service=service)
    for name, old_record in old_master.items():
        if name not in new_master:
            continue
        record = new_master[name]
        if old_record.contact_frequency_days is not None:
            record.contact_frequency_days = old_record.contact_frequency_days
        # 手動編集のみで日次入力からは決して集約されないフィールドは、常に引き継ぐ
        if old_record.profile_memo:
            record.profile_memo = old_record.profile_memo
        if old_record.company:
            record.company = old_record.company
        if old_record.position:
            record.position = old_record.position
        # 誕生日: 今回の日次入力に情報が無ければ、手動設定を含む既存の値を優先して保持する
        if record.birthday is None and old_record.birthday:
            record.birthday = old_record.birthday

    write_customer_master(spreadsheet_id, new_master, service=service)
    return new_master


def _find_master_row_number(spreadsheet_id: str, customer_id: str, service) -> int:
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{MASTER_TAB}!A2:A").execute()
    ids = [row[0] if row else "" for row in result.get("values", [])]
    if customer_id not in ids:
        raise ValueError(f"顧客IDが見つかりません: {customer_id}")
    return ids.index(customer_id) + 2  # ヘッダー行(1) + 0始まりのオフセット


def _update_master_cell(spreadsheet_id: str, customer_id: str, column_name: str, value: str, service=None) -> None:
    service = service or get_sheets_service()
    row_number = _find_master_row_number(spreadsheet_id, customer_id, service)
    column_index = MASTER_HEADERS.index(column_name)
    column_letter = chr(ord("A") + column_index)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{MASTER_TAB}!{column_letter}{row_number}",
        valueInputOption="RAW",
        body={"values": [[value]]},
    ).execute()


def update_contact_frequency(spreadsheet_id: str, customer_id: str, days: int | None, service=None) -> None:
    """顧客マスタの該当行の「連絡頻度日数」セルだけを更新する"""
    _update_master_cell(spreadsheet_id, customer_id, "連絡頻度日数", str(days) if days is not None else "", service=service)


def update_customer_profile(
    spreadsheet_id: str,
    customer_id: str,
    birthday: str | None = None,
    profile_memo: str | None = None,
    company: str | None = None,
    position: str | None = None,
    service=None,
) -> None:
    """顧客詳細ページからのプロフィール項目の更新（渡した項目だけ更新する）"""
    service = service or get_sheets_service()
    if birthday is not None:
        _update_master_cell(spreadsheet_id, customer_id, "誕生日", birthday, service=service)
    if profile_memo is not None:
        _update_master_cell(spreadsheet_id, customer_id, "プロフィールメモ", profile_memo, service=service)
    if company is not None:
        _update_master_cell(spreadsheet_id, customer_id, "会社名", company, service=service)
    if position is not None:
        _update_master_cell(spreadsheet_id, customer_id, "役職", position, service=service)


# ---------------------------------------------------------------------------
# カレンダー：共通予定（強制同伴日・イベント・ミーティング・新調日・連休）
# ---------------------------------------------------------------------------

def ensure_events_tab(spreadsheet_id: str, service=None) -> None:
    """プロセス内では一度だけ確認すればよい（毎回の一覧表示のたびに確認しない）"""
    if _already_ensured(EVENTS_TAB):
        return
    service = service or get_sheets_service()
    meta = _get_sheet_meta(spreadsheet_id, service)
    existing_titles = [s["properties"]["title"] for s in meta["sheets"]]
    tab_is_new = EVENTS_TAB not in existing_titles
    if tab_is_new:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [{"addSheet": {"properties": {"title": EVENTS_TAB}}}]}
        ).execute()

    existing = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{EVENTS_TAB}!1:1").execute()
    headers_are_new = not existing.get("values")
    if headers_are_new:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{EVENTS_TAB}!A1",
            valueInputOption="RAW", body={"values": [EVENTS_HEADERS]},
        ).execute()

    if tab_is_new or headers_are_new:
        sheet_id = (
            _sheet_id_from_meta(meta, EVENTS_TAB)
            if not tab_is_new
            else _get_sheet_id(spreadsheet_id, EVENTS_TAB, service)
        )
        requests = [
            _header_format_request(sheet_id, 0, len(EVENTS_HEADERS)),
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    _mark_ensured(EVENTS_TAB)


def add_event(spreadsheet_id: str, kind: str, start_date: date, end_date: date, title: str = "", service=None) -> None:
    service = service or get_sheets_service()
    ensure_events_tab(spreadsheet_id, service=service)
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=f"{EVENTS_TAB}!A2",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [[kind, start_date.isoformat(), end_date.isoformat(), title]]},
    ).execute()


def list_events_in_range(spreadsheet_id: str, range_start: date, range_end: date, service=None) -> list[CalendarEvent]:
    """指定した期間に重なる共通予定を返す（カレンダーの月表示で使う）"""
    service = service or get_sheets_service()
    ensure_events_tab(spreadsheet_id, service=service)
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{EVENTS_TAB}!A2:D").execute()
    rows = result.get("values", [])

    events = []
    for row in rows:
        row = row + [""] * (4 - len(row))
        kind, start_str, end_str, title = row[:4]
        start = _parse_date(start_str)
        end = _parse_date(end_str)
        if not (kind and start and end):
            continue
        if start <= range_end and end >= range_start:
            events.append(CalendarEvent(kind=kind, start_date=start, end_date=end, title=title))

    events.sort(key=lambda e: e.start_date)
    return events


# ---------------------------------------------------------------------------
# カレンダー：個人の予定（同伴・来店）。来店記録として入力されるまでの仮の予定
# ---------------------------------------------------------------------------

def ensure_plans_tab(spreadsheet_id: str, service=None) -> None:
    """プロセス内では一度だけ確認すればよい（毎回の一覧表示のたびに確認しない）"""
    if _already_ensured(PLANS_TAB):
        return
    service = service or get_sheets_service()
    meta = _get_sheet_meta(spreadsheet_id, service)
    existing_titles = [s["properties"]["title"] for s in meta["sheets"]]
    tab_is_new = PLANS_TAB not in existing_titles
    if tab_is_new:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": [{"addSheet": {"properties": {"title": PLANS_TAB}}}]}
        ).execute()

    existing = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{PLANS_TAB}!1:1").execute()
    headers_are_new = not existing.get("values")
    if headers_are_new:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{PLANS_TAB}!A1",
            valueInputOption="RAW", body={"values": [PLANS_HEADERS]},
        ).execute()

    if tab_is_new or headers_are_new:
        sheet_id = (
            _sheet_id_from_meta(meta, PLANS_TAB)
            if not tab_is_new
            else _get_sheet_id(spreadsheet_id, PLANS_TAB, service)
        )
        requests = [
            _header_format_request(sheet_id, 0, len(PLANS_HEADERS)),
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    _mark_ensured(PLANS_TAB)


def add_plan(spreadsheet_id: str, on_date: date, kind: str, name: str, service=None) -> None:
    service = service or get_sheets_service()
    ensure_plans_tab(spreadsheet_id, service=service)
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=f"{PLANS_TAB}!A2",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [[on_date.isoformat(), kind, name]]},
    ).execute()


def list_plans(spreadsheet_id: str, service=None) -> list[tuple[int, Plan]]:
    """個人の予定を全件 (行番号, Plan) の形で日付順に返す（記録済みかどうかはPlan.recordedで判別する）"""
    service = service or get_sheets_service()
    ensure_plans_tab(spreadsheet_id, service=service)
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{PLANS_TAB}!A2:D").execute()
    rows = result.get("values", [])

    plans = []
    for offset, row in enumerate(rows):
        row = row + [""] * (4 - len(row))
        date_str, kind, name, recorded_str = row[:4]
        parsed_date = _parse_date(date_str)
        if parsed_date and kind and name:
            row_number = 2 + offset
            plans.append((row_number, Plan(date=parsed_date, kind=kind, name=name, recorded=recorded_str == "有")))

    plans.sort(key=lambda item: item[1].date)
    return plans


def get_plan(spreadsheet_id: str, row_number: int, service=None) -> Plan | None:
    service = service or get_sheets_service()
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{PLANS_TAB}!A{row_number}:D{row_number}").execute()
    values = result.get("values", [[]])
    row = values[0] if values else []
    row = row + [""] * (4 - len(row))
    date_str, kind, name, recorded_str = row[:4]
    parsed_date = _parse_date(date_str)
    if parsed_date and kind and name:
        return Plan(date=parsed_date, kind=kind, name=name, recorded=recorded_str == "有")
    return None


def mark_plan_recorded(spreadsheet_id: str, row_number: int, service=None) -> None:
    """個人の予定を「記録済み」にする（来店記録として入力し終えたら呼ぶ。カレンダー表示からは消さない）"""
    service = service or get_sheets_service()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{PLANS_TAB}!D{row_number}",
        valueInputOption="RAW", body={"values": [["有"]]},
    ).execute()
