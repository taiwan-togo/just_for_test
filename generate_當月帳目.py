#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_當月帳目.py  ──  當月帳目報表 生產線腳本（可改骨架）

這是「報表系統」待辦「當月帳目」的凍結腳本重寫版，內含老闆拍板、
交接文件(HANDOFF_當月帳目報表_2026-07-08)裡尚未實作的「活頁三件事」：
  (1) 彙整升級成「當月 + 累計」兩欄，且列出所有科目（當月沒動的累計照列，當月欄放「—」）。
  (2) 明細加 --scope month|full：month=只印本月逐筆（每月新增用，預設）；full=含以前全部（備用）。
  (3) 累計 = 開帳～當月止，同規則分組彙總，沿用內建對帳。

原則（四道防線，勿破壞）：
  1. 數字由 code 算，不由 AI 讀 → 同輸入=同輸出。
  2. Ragic 為唯一真相，每次現抓，不靠記憶重建。
  3. 內建對帳：逐筆總和 == 廠商小計和 == 科目小計 == 區塊小計和 == 總計，不符即中止不出檔。
  4. 品名翻譯只判斷一次，存 品名對照.json，永遠沿用。

⚠️ 這份是「給你改」的骨架：真正要你動的東西全部集中在下面 CONFIG 區塊、標了 # TODO。
   本次跑法「跟苧麻沒關」──預設不綁任何出國專案（--project 空白）。

跑法（每月只換月份）：
  python3 generate_當月帳目.py --month 2026/07 --code "TG-202607-月帳" \
      --outdir "../產出" --scope month --pdf

列印一律印 --pdf 出的 PDF（Chrome 無頭、A5 多頁、無頁首頁尾）。不要印 claude.ai artifact。
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import OrderedDict, defaultdict
from datetime import datetime

# =============================================================================
# CONFIG ── 你要改的都在這裡
# =============================================================================

# --- Ragic 連線 -----------------------------------------------------------
# Key 只放本機 ~/.ragic_key（一行純文字），不進雲端、不寫死在這裡。
RAGIC_KEY_PATH = os.path.expanduser("~/.ragic_key")
RAGIC_DOMAIN = "https://ap12.ragic.com"          # TODO: 換成你帳號實際網域（如 www / ap5 / ap12）
RAGIC_ACCOUNT = "your-account"                    # TODO: 換成你的 Ragic 帳號名

# 付款(金流)主表：交接文件註明全帳號只有 bookkeeping/4 引用會計對象碼。
BOOKKEEPING_SHEET = "bookkeeping/4"               # TODO: 確認付款表路徑
# 對象主表(forms3/2)：拿業務歸屬/對象屬性(排除科目用)。
OBJECT_SHEET = "forms3/2"                          # TODO: 確認對象主表路徑

# --- 欄位對照（Ragic 欄位「顯示名」或欄位 id，二擇一，跟你資料一致即可）-----
# TODO: 全部對照成你 bookkeeping/4 的真實欄位名。抓不到就會在對帳前示警。
F = {
    "date":        "日期",          # 西元日期，格式假設 YYYY/MM/DD
    "amount":      "金額",          # 數字（支出為正）
    "direction":   "收支",          # 用來只留「支出」；值見 EXPENSE_VALUES
    "account":     "會計科目",       # 科目名（對齊標準版）
    "object":      "對象",          # 廠商/協作者/客戶名
    "biz":         "業務歸屬",       # 布衣 / 土狗 / 待確認 / 私人 …
    "summary":     "摘要",          # 明細摘要
    "note":        "我的註記",       # 出國專案辨識用（本次不綁專案，仍讀進來備查）
    "obj_attr":    "對象屬性",       # 排除用：銀行往來 / 全職 …
}

EXPENSE_VALUES = {"支出", "expense", "out"}        # F[direction] 命中這些才算支出
BIZ_KEEP = {"布衣", "土狗", "待確認"}               # 業務歸屬留這些；私人排除。土狗併入布衣對帳。
BIZ_MERGE_INTO = {"土狗": "布衣"}                   # 土狗開單多用布衣，對帳時併入布衣
EXCLUDE_OBJ_ATTR = {"銀行往來", "全職"}             # 對象屬性命中即排除
BLANK_ACCOUNT_TOKENS = {"", "未分類", None}         # 空白(未分類)不列入、改示警

# --- 四區塊分類 -----------------------------------------------------------
# 布衣業務科目（各協力委外＋原料＋釘釦＋成品）。原料=材料/紗線/纖維併。
BUYI_ACCOUNTS = {
    "原料", "材料", "紗線", "纖維",   # → 全部歸「原料」顯示
    "裁剪", "車縫", "染整", "整燙", "成品", "釘釦", "打版",
}
# 材料/紗線/纖維 顯示併成「原料」
BUYI_ALIAS = {"材料": "原料", "紗線": "原料", "纖維": "原料"}
# 布衣業務科目固定序（其餘按金額大到小）
BUYI_ORDER = ["原料", "裁剪", "車縫", "染整", "整燙", "成品", "釘釦", "打版"]

# 會計項目（營業費用類）
ACCOUNTING_ACCOUNTS = {
    "租金支出", "訂閱", "差旅費", "伙食費", "文具用品費", "交通費", "通訊費",
    "水電", "訓練費", "印刷", "工業類", "硬體設備", "空間", "食品材料",
    "保險費", "稅捐", "交際費", "捐贈",
}
# 員工薪資（協作者）：靠對象屬性判斷，或科目名
SALARY_ACCOUNTS = {"薪資", "員工薪資", "協作者"}

# 專案業務：出國專案（差旅費，靠「我的註記」辨識）。本次不綁專案 → 預設空，仍保留區塊機制。
PROJECT_NOTE_KEYS = []   # e.g. ["韓國", "日本"]；空=不切專案區塊

BLOCK_ORDER = ["布衣業務", "會計項目", "員工薪資", "專案業務"]

# --- 品名對照（判斷只做一次，永遠沿用）------------------------------------
NAME_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "品名對照.json")

# --- 版面（凍結規格）------------------------------------------------------
FONT_BODY_PT = 14      # 內文
FONT_ITEM_PT = 12      # 細項
FONT_VENDOR_PT = 14    # 廠商（粗體）
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/opt/pw-browsers/chromium",
    "google-chrome", "chromium", "chromium-browser",
]

# =============================================================================
# 以下為邏輯本體，通常不用改
# =============================================================================


def die(msg):
    print(f"✗ 中止：{msg}", file=sys.stderr)
    sys.exit(1)


def load_ragic_key():
    if not os.path.exists(RAGIC_KEY_PATH):
        die(f"找不到 Ragic key：{RAGIC_KEY_PATH}（key 只放本機，一行純文字）")
    with open(RAGIC_KEY_PATH, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key:
        die("Ragic key 檔是空的")
    return key


def ragic_fetch(sheet, key, where=None):
    """抓一張 Ragic 表，回傳 list[dict]。Ragic 回傳是 {rowid: {field: val}}。"""
    params = {"api": "", "APIKey": key, "v": "3"}
    if where:
        # where 形如 [("欄位id","eq","值"), ...]；多數情況本腳本在本地過濾，這裡先全抓。
        for i, (fld, op, val) in enumerate(where):
            params[f"where"] = f"{fld},{op},{val}"
    url = f"{RAGIC_DOMAIN}/{RAGIC_ACCOUNT}/{sheet}?{urllib.parse.urlencode(params)}"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        die(f"抓 Ragic 失敗（{sheet}）：{e}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        die(f"Ragic 回傳非 JSON（{sheet}），前 200 字：{raw[:200]}")
    if isinstance(data, dict):
        rows = [v for k, v in data.items() if k.lstrip("-").isdigit()]
    else:
        rows = list(data)
    return rows


def g(row, key_name, default=""):
    """容錯取欄位：先試顯示名，取不到回 default。"""
    if key_name in row:
        return row[key_name]
    return default


def parse_amount(v):
    if v is None:
        return 0.0
    s = str(v).replace(",", "").replace("$", "").strip()
    if s in ("", "-", "—"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_date(v):
    s = str(v or "").strip()[:10]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def load_name_map():
    if os.path.exists(NAME_MAP_PATH):
        with open(NAME_MAP_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def normalize_account(acc):
    acc = (acc or "").strip()
    return BUYI_ALIAS.get(acc, acc)


def classify_block(acc, obj_attr, note):
    acc = normalize_account(acc)
    if acc in BUYI_ACCOUNTS or acc in BUYI_ALIAS.values():
        return "布衣業務", acc
    if acc in SALARY_ACCOUNTS or (obj_attr or "").strip() in {"協作者", "員工"}:
        return "員工薪資", acc or "薪資"
    if PROJECT_NOTE_KEYS and any(k in (note or "") for k in PROJECT_NOTE_KEYS):
        return "專案業務", acc
    if acc in ACCOUNTING_ACCOUNTS:
        return "會計項目", acc
    # 落在已知集合外但有科目 → 仍當會計項目，方便 review（不會靜默吞）
    return "會計項目", acc


def keep_row(row):
    """回傳 (keep: bool, reason: str)。"""
    if str(g(row, F["direction"])).strip() not in EXPENSE_VALUES:
        return False, "非支出"
    biz = str(g(row, F["biz"])).strip()
    if biz and biz not in BIZ_KEEP:
        return False, f"業務歸屬={biz}(排除)"
    if str(g(row, F["obj_attr"])).strip() in EXCLUDE_OBJ_ATTR:
        return False, "對象屬性排除"
    return True, ""


def rows_to_records(rows):
    """轉成標準 record，並收集未分類示警。"""
    recs, blank = [], []
    for r in rows:
        keep, _ = keep_row(r)
        if not keep:
            continue
        acc_raw = str(g(r, F["account"])).strip()
        rec = {
            "date": parse_date(g(r, F["date"])),
            "amount": parse_amount(g(r, F["amount"])),
            "account": normalize_account(acc_raw),
            "object": str(g(r, F["object"])).strip() or "(未填廠商)",
            "biz": str(g(r, F["biz"])).strip(),
            "obj_attr": str(g(r, F["obj_attr"])).strip(),
            "summary": str(g(r, F["summary"])).strip(),
            "note": str(g(r, F["note"])).strip(),
        }
        if acc_raw in BLANK_ACCOUNT_TOKENS or not acc_raw:
            blank.append(rec)
            continue
        recs.append(rec)
    return recs, blank


def r2(x):
    """四捨五入到整數（紙上每行加起來要剛好等於小計）。"""
    return int(round(x + 1e-9))


def month_bounds(month_str):
    y, m = month_str.split("/")
    y, m = int(y), int(m)
    start = datetime(y, m, 1)
    end = datetime(y + (m // 12), (m % 12) + 1, 1)  # 下月一號
    return start, end


def sum_display(recs):
    """小計 = 各筆顯示值(四捨五入後)之和。"""
    return sum(r2(x["amount"]) for x in recs)


def group_by_account(recs):
    """回傳 {block: {account: [recs...]}}，含布衣固定序。"""
    tree = defaultdict(lambda: defaultdict(list))
    for rec in recs:
        block, acc = classify_block(rec["account"], rec["obj_attr"], rec["note"])
        tree[block][acc].append(rec)
    return tree


def ordered_accounts(block, accounts):
    if block == "布衣業務":
        fixed = [a for a in BUYI_ORDER if a in accounts]
        rest = sorted([a for a in accounts if a not in BUYI_ORDER],
                      key=lambda a: -sum_display(accounts[a]))
        return fixed + rest
    return sorted(accounts, key=lambda a: -sum_display(accounts[a]))


def reconcile(tree, label):
    """內建對帳：逐筆和 == 科目小計 == 區塊小計和 == 總計。不符 die。"""
    grand_line = 0
    grand_block = 0
    for block in tree:
        block_line = 0
        block_sub = 0
        for acc, recs in tree[block].items():
            line = sum(r2(x["amount"]) for x in recs)
            sub = sum_display(recs)
            if line != sub:
                die(f"[{label}] 對帳失敗：{block}/{acc} 逐筆和({line}) != 科目小計({sub})")
            block_line += line
            block_sub += sub
        if block_line != block_sub:
            die(f"[{label}] 對帳失敗：{block} 區塊逐筆和 != 區塊小計")
        grand_line += block_line
        grand_block += block_sub
    if grand_line != grand_block:
        die(f"[{label}] 對帳失敗：總逐筆和 != 總計")
    return grand_block


def block_total(tree, block):
    return sum(sum_display(recs) for recs in tree.get(block, {}).values())


def account_total(tree, block, acc):
    return sum_display(tree.get(block, {}).get(acc, []))


# ---------- 彙整：當月 + 累計 兩欄，列出所有科目 -----------------------------
def build_summary(tree_month, tree_cumu):
    """
    回傳有序 [(block, acc, month_val_or_None, cumu_val)]。
    當月沒動、但累計有的科目 → 照列，month 放 None（顯示「—」）。
    """
    rows = []
    all_blocks = OrderedDict((b, None) for b in BLOCK_ORDER)
    for b in list(tree_month) + list(tree_cumu):
        all_blocks.setdefault(b, None)
    for block in all_blocks:
        accs = set(tree_month.get(block, {})) | set(tree_cumu.get(block, {}))
        # 排序沿用累計金額（比較穩定）
        for acc in ordered_accounts(block, {a: tree_cumu.get(block, {}).get(a, [])
                                            for a in accs}):
            m = account_total(tree_month, block, acc)
            c = account_total(tree_cumu, block, acc)
            rows.append((block, acc, (m if m else None), c))
    return rows


# ---------- HTML / PDF ------------------------------------------------------
def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fmt(n):
    if n is None:
        return "—"
    return f"{n:,}"


def html_summary_page(title, period, maker, code, summary_rows, grand_m, grand_c):
    tr = []
    cur_block = None
    for block, acc, m, c in summary_rows:
        if block != cur_block:
            tr.append(f'<tr class="blk"><td colspan="3">{esc(block)}</td></tr>')
            cur_block = block
        tr.append(
            f'<tr><td class="acc">{esc(acc)}</td>'
            f'<td class="num">{fmt(m)}</td>'
            f'<td class="num">{fmt(c)}</td></tr>'
        )
    return f"""
<section class="page summary">
  <h1>{esc(title)}</h1>
  <div class="meta">期間：{esc(period)}　｜　製表：{esc(maker)}　｜　編號：{esc(code)}</div>
  <table class="sum">
    <thead><tr><th>會計科目</th><th class="num">當月</th><th class="num">累計</th></tr></thead>
    <tbody>{''.join(tr)}</tbody>
    <tfoot><tr class="grand"><td>總計</td>
      <td class="num">{fmt(grand_m)}</td><td class="num">{fmt(grand_c)}</td></tr></tfoot>
  </table>
</section>"""


def html_detail_pages(tree):
    """每科目一張 A5：廠商｜日期｜摘要｜金額；廠商小計灰底加粗下壓黑線。"""
    pages = []
    for block in BLOCK_ORDER:
        if block not in tree:
            continue
        accs = tree[block]
        for acc in ordered_accounts(block, accs):
            recs = accs[acc]
            # 廠商分組，廠商按最近往來 新→舊
            byv = defaultdict(list)
            for r in recs:
                byv[r["object"]].append(r)
            vendors = sorted(
                byv,
                key=lambda v: max((x["date"] or datetime.min) for x in byv[v]),
                reverse=True,
            )
            body = []
            for v in vendors:
                items = sorted(byv[v], key=lambda x: (x["date"] or datetime.min), reverse=True)
                for x in items:
                    d = x["date"].strftime("%Y/%m/%d") if x["date"] else ""
                    body.append(
                        f'<tr><td class="v">{esc(v)}</td><td>{esc(d)}</td>'
                        f'<td>{esc(x["summary"])}</td>'
                        f'<td class="num">{fmt(r2(x["amount"]))}</td></tr>'
                    )
                vs = sum(r2(x["amount"]) for x in items)
                body.append(
                    f'<tr class="vsub"><td colspan="3">{esc(v)} 小計</td>'
                    f'<td class="num">{fmt(vs)}</td></tr>'
                )
            asub = sum_display(recs)
            pages.append(f"""
<section class="page detail">
  <h2>{esc(block)}／{esc(acc)}</h2>
  <table class="det">
    <thead><tr><th>廠商</th><th>日期</th><th>摘要</th><th class="num">金額</th></tr></thead>
    <tbody>{''.join(body)}</tbody>
    <tfoot><tr class="asub"><td colspan="3">{esc(acc)} 科目小計</td>
      <td class="num">{fmt(asub)}</td></tr></tfoot>
  </table>
</section>""")
    return pages


CSS = f"""
@page {{ size: A5; margin: 10mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "PingFang TC","Heiti TC","Microsoft JhengHei",sans-serif;
       color:#000; font-size:{FONT_BODY_PT}pt; margin:0; }}
.page {{ page-break-after: always; padding: 0; }}
h1 {{ font-size:{FONT_BODY_PT+4}pt; margin:0 0 4mm; }}
h2 {{ font-size:{FONT_BODY_PT+1}pt; margin:0 0 3mm; }}
.meta {{ font-size:{FONT_ITEM_PT}pt; margin-bottom:4mm; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{ padding:1.5mm 2mm; font-size:{FONT_ITEM_PT}pt; vertical-align:top; }}
thead th {{ border-bottom:1px solid #000; text-align:left; background:#eee; }}
.num {{ text-align:right; white-space:nowrap; }}
.acc {{ padding-left:4mm; }}
tr.blk td {{ background:#e8e8e8; font-weight:bold; font-size:{FONT_BODY_PT}pt; }}
td.v {{ font-size:{FONT_VENDOR_PT}pt; font-weight:bold; }}
tr.vsub td {{ background:#eee; font-weight:bold; border-bottom:1.5px solid #000; }}
tr.asub td, tr.grand td {{ font-weight:bold; border-top:1.5px solid #000; }}
"""


def render_html(title, period, maker, code, summary_rows, grand_m, grand_c, detail_pages):
    parts = [html_summary_page(title, period, maker, code, summary_rows, grand_m, grand_c)]
    parts += detail_pages
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>{esc(title)}</title><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"""


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.sep in c and os.path.exists(c):
            return c
        if os.path.sep not in c:
            from shutil import which
            p = which(c)
            if p:
                return p
    return None


def to_pdf(html_path, pdf_path):
    chrome = find_chrome()
    if not chrome:
        die("找不到 Chrome/Chromium，無法出 PDF（可先用 --no-pdf 產 HTML）")
    cmd = [chrome, "--headless=new", "--no-pdf-header-footer", "--disable-gpu",
           f"--print-to-pdf={pdf_path}", f"file://{os.path.abspath(html_path)}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(pdf_path):
        die(f"Chrome 出 PDF 失敗：{r.stderr[:300]}")


# ---------- main ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="當月帳目報表產生器")
    ap.add_argument("--month", required=True, help="西元月份，如 2026/07")
    ap.add_argument("--code", required=True, help="系統編號，如 TG-202607-月帳")
    ap.add_argument("--outdir", default="../產出")
    ap.add_argument("--title", default="當月帳目報表")
    ap.add_argument("--maker", default="")
    ap.add_argument("--scope", choices=["month", "full"], default="month",
                    help="month=只印本月逐筆(預設)；full=含以前全部")
    ap.add_argument("--project", default="",
                    help="出國專案（我的註記關鍵字）；本次不綁專案就留空")
    ap.add_argument("--pdf", action="store_true", help="出 A5 PDF")
    args = ap.parse_args()

    if args.project:
        PROJECT_NOTE_KEYS.append(args.project)

    key = load_ragic_key()
    print("→ 現抓 Ragic …")
    rows = ragic_fetch(BOOKKEEPING_SHEET, key)
    recs_all, blank = rows_to_records(rows)
    if blank:
        print(f"⚠ 未分類 {len(blank)} 筆（空白科目，未列入，待老闆補歸類）")

    start, end = month_bounds(args.month)
    recs_month = [r for r in recs_all if r["date"] and start <= r["date"] < end]
    recs_cumu = [r for r in recs_all if r["date"] and r["date"] < end]  # 開帳~當月止
    recs_before = [r for r in recs_all if r["date"] and r["date"] < start]

    tree_month = group_by_account(recs_month)
    tree_cumu = group_by_account(recs_cumu)

    grand_m = reconcile(tree_month, "當月")
    grand_c = reconcile(tree_cumu, "累計")
    print(f"→ 對帳通過：當月 {grand_m:,}／累計 {grand_c:,}")

    summary_rows = build_summary(tree_month, tree_cumu)

    # 明細範圍
    if args.scope == "month":
        detail_tree = tree_month
    else:
        detail_tree = group_by_account(recs_cumu)
    detail_pages = html_detail_pages(detail_tree)

    period = args.month.replace("/", "年") + "月"
    html = render_html(args.title, period, args.maker, args.code,
                       summary_rows, grand_m, grand_c, detail_pages)

    os.makedirs(args.outdir, exist_ok=True)
    base = f"{args.code}_當月帳目_{args.month.replace('/', '')}"
    html_path = os.path.join(args.outdir, base + ".html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"✓ HTML：{html_path}")

    if args.pdf:
        pdf_path = os.path.join(args.outdir, base + ".pdf")
        to_pdf(html_path, pdf_path)
        print(f"✓ PDF ：{pdf_path}（{len(detail_pages)} 張明細頁＋1 張彙整）")


if __name__ == "__main__":
    main()
