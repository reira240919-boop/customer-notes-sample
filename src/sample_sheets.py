"""見本（サンプル）用の作り物のスプレッドシート

本物の Google スプレッドシートには一切つながず、プロセスのメモリーの中だけで読み書きする。
Google Sheets API の service と同じ呼び方（spreadsheets().values().get(...).execute() など）を
このアプリで使う分だけ真似しているので、sheets_client.py の関数はそのまま動く。

サーバーを起動し直すと、作り物のデータは最初の状態に戻る（見本で入力した内容は残らない）。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

SAMPLE_SPREADSHEET_ID = "sample"


def is_sample(spreadsheet_id: str | None) -> bool:
    return not spreadsheet_id or spreadsheet_id == SAMPLE_SPREADSHEET_ID


# ---- 範囲（"タブ名!A2:Z" など）の読み取り ----

def _col_to_index(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


_CELL = re.compile(r"^([A-Z]*)(\d*)$")


def _parse_range(a1: str) -> tuple[str, int, int | None, int, int | None]:
    """(タブ名, 開始行, 終了行, 開始列, 終了列) を 0 始まりで返す。終わりが無い範囲は None"""
    tab, _, cells = a1.rpartition("!")
    if not tab:
        tab, cells = cells, ""
    tab = tab.strip("'")
    if not cells:
        return tab, 0, None, 0, None
    start, _, end = cells.partition(":")
    s_col, s_row = _CELL.match(start).groups()
    start_row = int(s_row) - 1 if s_row else 0
    start_col = _col_to_index(s_col) if s_col else 0
    if not end:
        # 1セルだけ（例: A2）
        return tab, start_row, start_row, start_col, None if not s_col else start_col
    e_col, e_row = _CELL.match(end).groups()
    end_row = int(e_row) - 1 if e_row else None
    end_col = _col_to_index(e_col) if e_col else None
    return tab, start_row, end_row, start_col, end_col


class _Request:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Values:
    def __init__(self, book: "SampleSheetsService"):
        self.book = book

    def _read(self, a1: str) -> dict:
        tab, r0, r1, c0, c1 = _parse_range(a1)
        rows = self.book.tabs.get(tab, [])
        last = len(rows) - 1 if r1 is None else min(r1, len(rows) - 1)
        out = []
        for r in range(r0, last + 1):
            row = rows[r]
            cells = row[c0:] if c1 is None else row[c0:c1 + 1]
            cells = [str(v) for v in cells]
            while cells and cells[-1] == "":
                cells.pop()
            out.append(cells)
        while out and not out[-1]:
            out.pop()
        result = {"range": a1}
        if out:
            result["values"] = out
        return result

    # Google の呼び方に合わせて range= で受け取る（Python の range と名前がぶつかるので kwargs で受ける）
    def get(self, spreadsheetId, **kw):
        return _Request(self._read(kw["range"]))

    def batchGet(self, spreadsheetId, ranges, **_):
        return _Request({"valueRanges": [self._read(r) for r in ranges]})

    def update(self, spreadsheetId, body, **kw):
        tab, r0, _, c0, _ = _parse_range(kw["range"])
        rows = self.book.tabs.setdefault(tab, [])
        for i, values in enumerate(body.get("values", [])):
            while len(rows) <= r0 + i:
                rows.append([])
            row = rows[r0 + i]
            while len(row) < c0 + len(values):
                row.append("")
            for j, v in enumerate(values):
                row[c0 + j] = "" if v is None else str(v)
        return _Request({})

    def append(self, spreadsheetId, body, **kw):
        tab, r0, _, c0, _ = _parse_range(kw["range"])
        rows = self.book.tabs.setdefault(tab, [])
        # 表の最後の、何か入っている行の次に足す
        last_filled = r0 - 1
        for i in range(r0, len(rows)):
            if any(str(v).strip() for v in rows[i]):
                last_filled = i
        start = max(last_filled + 1, r0)
        for i, values in enumerate(body.get("values", [])):
            while len(rows) <= start + i:
                rows.append([])
            row = [""] * c0 + [("" if v is None else str(v)) for v in values]
            rows[start + i] = row
        return _Request({})

    def clear(self, spreadsheetId, **kw):
        tab, r0, r1, c0, c1 = _parse_range(kw["range"])
        rows = self.book.tabs.get(tab, [])
        last = len(rows) - 1 if r1 is None else min(r1, len(rows) - 1)
        for r in range(r0, last + 1):
            row = rows[r]
            end = len(row) if c1 is None else min(c1 + 1, len(row))
            for c in range(c0, end):
                row[c] = ""
        return _Request({})


class _Spreadsheets:
    def __init__(self, book: "SampleSheetsService"):
        self.book = book

    def values(self):
        return _Values(self.book)

    def get(self, spreadsheetId, **_):
        sheets = [{"properties": {"sheetId": sid, "title": title}} for title, sid in self.book.sheet_ids.items()]
        return _Request({"sheets": sheets})

    def batchUpdate(self, spreadsheetId, body, **_):
        replies = []
        for req in body.get("requests", []):
            if "addSheet" in req:
                title = req["addSheet"]["properties"]["title"]
                sid = self.book.add_tab(title)
                replies.append({"addSheet": {"properties": {"sheetId": sid, "title": title}}})
                continue
            if "updateSheetProperties" in req:
                props = req["updateSheetProperties"]["properties"]
                if "title" in props:
                    self.book.rename_tab(props["sheetId"], props["title"])
            elif "deleteDimension" in req:
                rng = req["deleteDimension"]["range"]
                if rng.get("dimension") == "ROWS":
                    title = self.book.title_of(rng["sheetId"])
                    del self.book.tabs[title][rng["startIndex"]:rng["endIndex"]]
            # 書式・入力規則・列幅などの見た目の指定は、見本では何もしない
            replies.append({})
        return _Request({"replies": replies})


class SampleSheetsService:
    def __init__(self):
        self.tabs: dict[str, list[list[str]]] = {}
        self.sheet_ids: dict[str, int] = {}
        self._next_id = 1

    def spreadsheets(self):
        return _Spreadsheets(self)

    def add_tab(self, title: str) -> int:
        if title not in self.sheet_ids:
            self.sheet_ids[title] = self._next_id
            self._next_id += 1
            self.tabs[title] = []
        return self.sheet_ids[title]

    def rename_tab(self, sheet_id: int, new_title: str) -> None:
        old = self.title_of(sheet_id)
        self.tabs[new_title] = self.tabs.pop(old)
        self.sheet_ids = {(new_title if t == old else t): i for t, i in self.sheet_ids.items()}

    def title_of(self, sheet_id: int) -> str:
        for title, sid in self.sheet_ids.items():
            if sid == sheet_id:
                return title
        raise ValueError(f"タブが見つかりません: {sheet_id}")


# ---- 作り物のデータ ----

def seed(service: SampleSheetsService, initial_password: str) -> None:
    """アプリ自身の関数で各タブを作り、架空のお客様と来店記録を入れる"""
    from . import sheets_client as sc

    sid = SAMPLE_SPREADSHEET_ID
    # 本物では最初からあるタブ（アプリは作らずに使う）を、先に作っておく
    service.add_tab(sc.MASTER_TAB)
    sc.ensure_auth_tab(sid, initial_password, service=service)
    sc.ensure_staff_tab(sid, service=service)
    sc.ensure_master_tab(sid, service=service)
    sc.ensure_events_tab(sid, service=service)
    sc.ensure_plans_tab(sid, service=service)

    today = date.today()
    start = today.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    end = next_month - timedelta(days=1)
    sc.create_period_tab(sid, sc.period_label_from_dates(start, end), start, end, service=service)
    own_tab = sc.latest_period_tab(sid, is_own_customer=True, service=service)
    other_tab = sc.latest_period_tab(sid, is_own_customer=False, service=service)

    def d(days_ago: int) -> str:
        return (today - timedelta(days=days_ago)).isoformat()

    # 自客: [日付, 名前, 同伴, 同伴メモ, 同伴者, 連絡先, 売上, 最終連絡日, 誕生日, メモ]
    own_rows = [
        [d(40), "サンプル 一郎", "無", "", "", "有", "35000", d(40), "", "お酒は焼酎のお湯割り"],
        [d(30), "テスト 健", "有", "和食で食事", "", "有", "52000", d(30), (today + timedelta(days=6)).strftime("%m-%d"), "ゴルフの話が好き"],
        [d(21), "サンプル 次郎", "無", "", "テスト 二郎:有", "有", "41000", d(21), "", "同僚と来店"],
        [d(18), "サンプル 一郎", "無", "", "", "有", "28000", d(18), "", ""],
        [d(9), "見本 太郎", "無", "", "", "有", "33000", d(9), (today + timedelta(days=11)).strftime("%m-%d"), "出張帰り"],
        [d(3), "テスト 健", "有", "焼肉", "", "有", "61000", d(3), "", ""],
    ]
    # 他客: [日付, 係, 名前, 新規既存, 同伴, 同伴メモ, 同伴者, 連絡先, 売上, 最終連絡日, 誕生日, メモ]
    other_rows = [
        [d(25), "スタッフA", "サンプル 三郎", "既存", "無", "", "", "有", "", d(25), "", ""],
        [d(12), "スタッフB", "見本 次郎", "新規", "無", "", "", "無", "", "", "", "はじめての来店"],
        [d(5), "スタッフA", "テスト 四郎", "既存", "無", "", "", "有", "", d(5), "", ""],
    ]
    for row in own_rows:
        sc.append_daily_entry_row(sid, own_tab, row, service=service)
    for row in other_rows:
        sc.append_daily_entry_row(sid, other_tab, row, service=service)
    sc.refresh_customer_master(sid, service=service)

    sc.add_event(sid, "強制同伴日", today + timedelta(days=4), today + timedelta(days=4), "", service=service)
    sc.add_event(sid, "イベント", today + timedelta(days=10), today + timedelta(days=12), "周年", service=service)
    sc.add_plan(sid, today + timedelta(days=2), "同伴", "サンプル 一郎", service=service)
    sc.add_plan(sid, today + timedelta(days=6), "来店", "見本 太郎", service=service)
