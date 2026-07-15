#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
當月帳目報表 產生器 (A5 直式封存版) v2 — 版面凍結 2026-07-08，活頁補丁 2026-07-15
================================================================
用途：每月固定輸出一份「當月帳目」A5 封存報表，版面每月必然一致。

v2 活頁補丁（老闆已拍板「紙本持續新增制」，見 HANDOFF_當月帳目報表_2026-07-08）：
 (1) 彙整升級成「當月 + 累計」兩欄、且列出所有科目（當月沒動的科目累計照列，當月欄放「—」）。
 (2) 明細加 --scope month|full：month=只印本月逐筆(每月新增用，預設)；full=當月含以前全部(備用)。
 (3) 累計＝開帳~當月止，同規則分組彙總，沿用內建對帳。

⚠️ 版面凍結部分（CSS / 欄位 / 順序 / 區塊 / 對帳）全部沿用 v1，未動。
   當月欄的算法刻意保持與 v1 相同（round(各筆加總)），確保重跑 2026/06 數字仍等於首跑基準 245,483。

設計原則（沿用費用結案生產線）：
 ① 數字由程式算，不由 AI 讀 —— 同輸入＝同輸出。
 ② Ragic 是唯一真相 —— 每次 API 現抓 bookkeeping/4，不靠記憶重建。
 ③ 內建對帳 —— 逐筆和 == 廠商小計和 == 科目小計 == 區塊小計和 == 總計；不符即中止不出檔。
 ④ 版面凍結在本檔 —— 改版＝改這支、升 version。

用法：
 python3 generate_當月帳目.py --month 2026/07 \\
     --code "TG-202607-月帳" --outdir "../產出" --scope month --pdf
 （--project 可重複，指定「我的註記」中屬出國專案者；本次不綁專案就不給。）
"""
import sys, os, json, argparse, urllib.request, subprocess
from collections import defaultdict

BASE = "https://ap2.ragic.com/jen3839/"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"  # mac；轉A5 PDF用

def to_pdf(html_path):
    """用 Chrome 無頭把 HTML 轉成 A5 多頁 PDF（無頁首頁尾）。"""
    pdf = os.path.splitext(html_path)[0] + ".pdf"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", "file://" + os.path.abspath(html_path)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return pdf

# ---- 凍結設定 ----
EXCL_SUBJ = {"銀行往來", "全職"}
IN_BIZ = {"布衣", "土狗", "待確認"}
BUYI = {"車縫","染整","整燙","裁剪","打版","原料-材料","原料-紗線","原料-纖維","釘釦","成品"}
BUYI_ORDER = ["原料","裁剪","車縫","染整","整燙","成品","釘釦","打版"]
P1_BLOCKS = ["布衣業務","會計項目","專案業務","員工薪資"]     # 彙整區塊序（含專案，僅彙整呈現）
P2_BLOCKS = ["布衣業務","會計項目","員工薪資"]               # 明細區塊序（不含專案）

def get_key():
    k = os.environ.get("RAGIC_KEY")
    if k: return k.strip()
    p = os.path.expanduser("~/.ragic_key")
    if os.path.exists(p):
        return open(p).read().strip()
    sys.exit("找不到 API Key：請設 RAGIC_KEY 或建立 ~/.ragic_key")

def fetch():
    url = BASE + "bookkeeping/4?api&v=3&limit=5000"
    req = urllib.request.Request(url, headers={"Authorization": "Basic " + get_key()})
    data = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
    return [v for v in data.values() if isinstance(v, dict)]

def blk_of(subj):
    if subj == "協作者": return "員工薪資"
    return "布衣業務" if subj in BUYI else "會計項目"

def raw(subj):
    return "原料" if (subj or "").startswith("原料-") else subj

def amt(r):
    try: return float(r.get("金額") or 0)
    except: return 0.0

def in_scope(r):
    return (r.get("收支類型") == "支出"
            and r.get("業務歸屬") in IN_BIZ
            and (r.get("對象屬性") or "") not in EXCL_SUBJ
            and (r.get("對象屬性") or "") != "")

def _date(r):
    return r.get("西元日期", "")

def _subject_sums(recs, month, projset, upto):
    """
    依「同規則」把 recs 分組成 {block: {subject: round(各筆加總)}}。
    upto=False → 只算當月(startswith month)；upto=True → 開帳~當月止(<= month)。
    is_proj 的記錄歸「專案業務」(以我的註記為科目)，其餘依 對象屬性 分區。
    小計採 round(Σ raw)，與 v1 彙整一致（保基準數字不變）。
    """
    is_proj = lambda r: (r.get("我的註記") or "").strip() in projset
    def hit(r):
        d = _date(r)
        return d.startswith(month) or (upto and d < month)
    acc = defaultdict(lambda: defaultdict(float))
    for r in recs:
        if not in_scope(r) or not hit(r):
            continue
        if is_proj(r):
            acc["專案業務"][(r.get("我的註記") or "").strip()] += amt(r)
        else:
            acc[blk_of(r.get("對象屬性"))][raw(r.get("對象屬性"))] += amt(r)
    return {b: {k: round(v) for k, v in subs.items()} for b, subs in acc.items()}

def build(recs, month, projects, scope):
    projset = set(projects or [])
    is_proj = lambda r: (r.get("我的註記") or "").strip() in projset

    # ---- 彙整：當月 + 累計 兩欄（補丁 1 + 3）----
    month_sub = _subject_sums(recs, month, projset, upto=False)   # 當月
    cumu_sub  = _subject_sums(recs, month, projset, upto=True)    # 開帳~當月止
    p1total   = round(sum(sum(s.values()) for s in month_sub.values()))
    cumutotal = round(sum(sum(s.values()) for s in cumu_sub.values()))

    # ---- 明細逐筆：依 --scope 決定範圍（補丁 2）----
    # month=只印本月逐筆；full=當月含以前全部。皆排除專案（專案只在彙整呈現）。
    def in_detail(r):
        d = _date(r)
        if scope == "month":
            return d.startswith(month)
        return d.startswith(month) or d < month   # full = <= 當月
    p2rec = [r for r in recs if in_scope(r) and not is_proj(r) and in_detail(r)]

    grp = defaultdict(lambda: defaultdict(list))
    for r in p2rec:
        grp[blk_of(r.get("對象屬性"))][raw(r.get("對象屬性"))].append(r)
    part2 = []
    for b in P2_BLOCKS:
        subjs = []
        for subj, rows in grp[b].items():
            objs = defaultdict(list)
            for r in rows:
                objs[r.get("對象名稱") or "（未填）"].append(r)
            og = []
            for name, orows in objs.items():
                orows.sort(key=lambda x: _date(x), reverse=True)
                det = [[_date(x), x.get("摘要",""), round(amt(x))] for x in orows]
                # 小計＝各筆「顯示值」之和 → 紙上每行加起來剛好等於小計
                og.append({"obj": name, "sum": sum(d[2] for d in det),
                           "last": orows[0].get("西元日期",""), "det": det})
            og.sort(key=lambda x: x["last"], reverse=True)   # 近→遠
            subjs.append({"blk": b, "subj": subj,
                          "sum": sum(o["sum"] for o in og), "objs": og})
        if b == "布衣業務":
            subjs.sort(key=lambda s: BUYI_ORDER.index(s["subj"]) if s["subj"] in BUYI_ORDER else 99)
        else:
            subjs.sort(key=lambda s: -s["sum"])
        part2 += subjs
    p2total = round(sum(s["sum"] for s in part2))
    return month_sub, p1total, cumu_sub, cumutotal, part2, p2total

def reconcile(month_sub, p1total, cumu_sub, cumutotal, part2, p2total):
    errs = []
    # 彙整：當月 / 累計 各自 區塊小計和 == 總計
    if round(sum(sum(s.values()) for s in month_sub.values())) != p1total:
        errs.append("彙整・當月 區塊小計和 ≠ 總計")
    if round(sum(sum(s.values()) for s in cumu_sub.values())) != cumutotal:
        errs.append("彙整・累計 區塊小計和 ≠ 總計")
    # 明細：逐筆 == 廠商小計 == 科目小計 == 總計
    for s in part2:
        if round(sum(o["sum"] for o in s["objs"])) != s["sum"]:
            errs.append(f"科目[{s['subj']}] 廠商小計和 ≠ 科目小計")
        for o in s["objs"]:
            if round(sum(d[2] for d in o["det"])) != o["sum"]:
                errs.append(f"廠商[{o['obj']}] 逐筆和 ≠ 廠商小計")
    if round(sum(s["sum"] for s in part2)) != p2total:
        errs.append("明細 科目小計和 ≠ 總計")
    if errs:
        sys.exit("對帳失敗，中止不出檔：\n - " + "\n - ".join(errs))

CSS = r"""
:root{ color-scheme:light; --ink:#141414; --sub:#555; --line:#111; --band:#e6e6e6; --sub2:#efefef; --paper:#fff; }
@page{ size:A5 portrait; margin:12mm 10mm; }
*{box-sizing:border-box} html,body{background:#8a8a8a;margin:0}
body{ font-family:"Songti TC","Noto Serif TC","Noto Serif CJK TC","Times New Roman",serif; color:var(--ink); font-size:14pt; line-height:1.4; }
.sheet{ background:var(--paper); width:148mm; min-height:210mm; max-width:calc(100% - 12px); margin:14px auto; padding:12mm 10mm; box-shadow:0 1px 6px rgba(0,0,0,.4); }
.co{ text-align:center; font-size:18pt; letter-spacing:.15em; font-weight:700; border-bottom:2px solid var(--line); padding-bottom:6px; }
.rpt-title{ text-align:center; font-size:15pt; font-weight:700; letter-spacing:.2em; margin:12px 0 12px;}
.n{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.boxh{ display:flex; justify-content:space-between; align-items:baseline; background:var(--band); font-weight:700; letter-spacing:.05em; padding:6px 12px; font-size:15pt; border-bottom:1px solid var(--line); }
/* ---- 彙整：當月＋累計 兩欄表（v2）---- */
table.sumtab{ border-collapse:collapse; width:100%; table-layout:fixed; border:1px solid var(--line); margin-bottom:10px; }
table.sumtab col.s{width:52%} table.sumtab col.m{width:24%} table.sumtab col.c{width:24%}
table.sumtab thead th{ background:var(--band); border-bottom:1px solid var(--line); font-size:13pt; padding:5px 12px; text-align:right; }
table.sumtab thead th.c-s{ text-align:left; }
tr.blkrow td{ background:var(--sub2); font-weight:700; font-size:14pt; padding:5px 12px; border-top:1px solid var(--line); }
tr.subjrow td{ padding:4px 12px 4px 30px; border-bottom:1px solid #eee; font-size:13pt; }
tr.grandrow td{ font-weight:700; font-size:16pt; border-top:2px solid var(--line); border-bottom:3px double var(--line); padding:8px 12px; }
.dash{ color:var(--sub); }
/* ---- 明細（沿用 v1）---- */
.p2box{ border:1px solid var(--line); }
table.led{ border-collapse:collapse; width:100%; table-layout:fixed; }
table.led th, table.led td{ padding:3px 8px; font-weight:400; vertical-align:top; overflow-wrap:anywhere; }
col.v{width:26%} col.d{width:24%} col.m{width:24%} col.a{width:26%}
table.led thead th{ border-bottom:1px solid var(--line); background:#fff; color:var(--sub); font-weight:700; font-size:13pt;}
table.led thead th.c-v{text-align:left} table.led thead th.c-d{text-align:left; padding-left:4px} table.led thead th.c-c{text-align:center} table.led thead th.n{text-align:right}
tr.det td{ border-bottom:1px solid #f4f4f4; color:#222; font-size:12pt;}
tr.det td.v{ font-weight:700; font-size:14pt; }
tr.det td.d{ text-align:left; padding-left:4px; color:var(--sub); white-space:nowrap;}
tr.det td.m{ text-align:center; color:var(--sub);}
tr.sub td{ background:var(--sub2); border-bottom:2px solid var(--line); font-size:13pt;}
tr.sub td.m{ text-align:right; font-weight:700; color:var(--ink);}
tr.sub td.n{ font-weight:700; color:#000; }
tr.sub:last-child td{ border-bottom:none; }
.pagebreak{ break-before:page; }
@media print{ html,body{background:#fff} .sheet{width:auto;min-height:0;max-width:none;margin:0;padding:0;box-shadow:none}
  thead{display:table-header-group} tr{break-inside:avoid} }
"""

def f(n): return f"{n:,}"
def cell(v): return f'<span class="dash">—</span>' if v is None else f(v)

def _ordered_subjects(block, month_b, cumu_b):
    """union of subjects in 當月/累計；布衣業務照固定序，其餘按累計大到小。"""
    keys = set(month_b) | set(cumu_b)
    if block == "布衣業務":
        fixed = [k for k in BUYI_ORDER if k in keys]
        rest = sorted([k for k in keys if k not in BUYI_ORDER], key=lambda k: -cumu_b.get(k, 0))
        return fixed + rest
    return sorted(keys, key=lambda k: -cumu_b.get(k, 0))

def render_summary(month_sub, p1total, cumu_sub, cumutotal, month):
    ym = month.replace("/", " 年 ") + " 月"
    rows = ""
    for b in P1_BLOCKS:
        month_b = month_sub.get(b, {})
        cumu_b  = cumu_sub.get(b, {})
        if not month_b and not cumu_b:
            continue
        m_bt = sum(month_b.values()) if month_b else None
        c_bt = sum(cumu_b.values())
        rows += (f'<tr class="blkrow"><td>{b}</td>'
                 f'<td class="n">{cell(m_bt)}</td><td class="n">{cell(c_bt)}</td></tr>')
        for k in _ordered_subjects(b, month_b, cumu_b):
            mv = month_b.get(k)          # None → 當月沒動 → 顯示「—」
            cv = cumu_b.get(k, 0)
            rows += (f'<tr class="subjrow"><td>{k}</td>'
                     f'<td class="n">{cell(mv)}</td><td class="n">{cell(cv)}</td></tr>')
    grand = (f'<tr class="grandrow"><td>總計</td>'
             f'<td class="n">{f(p1total)}</td><td class="n">{f(cumutotal)}</td></tr>')
    return (f'<div class="rpt-title">{ym} 彙 整（當月／累計）</div>'
            f'<table class="sumtab"><colgroup><col class="s"><col class="m"><col class="c"></colgroup>'
            f'<thead><tr><th class="c-s">會計科目</th><th>當月</th><th>累計</th></tr></thead>'
            f'<tbody>{rows}{grand}</tbody></table>')

def render(month_sub, p1total, cumu_sub, cumutotal, part2, month):
    summary = render_summary(month_sub, p1total, cumu_sub, cumutotal, month)
    sheets = ""
    for s in part2:
        body = ""
        for o in s["objs"]:
            for i, (dt, memo, a) in enumerate(o["det"]):
                v = o["obj"] if i == 0 else ""
                body += (f'<tr class="det"><td class="v">{v}</td><td class="d">{dt}</td>'
                         f'<td class="m">{memo}</td><td class="n">{f(a)}</td></tr>')
            body += (f'<tr class="sub"><td class="v"></td><td class="d"></td>'
                     f'<td class="m">小計</td><td class="n">{f(o["sum"])}</td></tr>')
        sheets += ('<div class="sheet pagebreak">'
                   '<div class="co">惠 中 布 衣 文 創</div>'
                   '<div class="rpt-title">支 出 明 細</div>'
                   '<div class="p2box">'
                   f'<div class="boxh"><span>{s["blk"]}・{s["subj"]}</span>'
                   f'<span class="n">小計 {f(s["sum"])}</span></div>'
                   '<table class="led"><colgroup><col class="v"><col class="d"><col class="m"><col class="a"></colgroup>'
                   '<thead><tr><th class="c-v">廠商</th><th class="c-d">日期</th>'
                   '<th class="c-c">摘要</th><th class="n">金額</th></tr></thead>'
                   f'<tbody>{body}</tbody></table></div></div>')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>惠中布衣 當月帳目報表 {month}</title>'
            f'<style>{CSS}</style></head><body>'
            '<div class="sheet"><div class="co">惠 中 布 衣 文 創</div>'
            f'{summary}</div>{sheets}</body></html>')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="當月 YYYY/MM，例 2026/07")
    ap.add_argument("--project", action="append", default=[], help="我的註記中屬專案者(可重複)")
    ap.add_argument("--code", default="", help="報表編號，作為檔名前綴")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--scope", choices=["month","full"], default="month",
                    help="month=只印本月逐筆(預設)；full=當月含以前全部")
    ap.add_argument("--pdf", action="store_true", help="同時輸出列印用 A5 PDF（Chrome 無頭）")
    args = ap.parse_args()

    recs = fetch()
    month_sub, p1total, cumu_sub, cumutotal, part2, p2total = build(
        recs, args.month, args.project, args.scope)
    reconcile(month_sub, p1total, cumu_sub, cumutotal, part2, p2total)   # 不符即中止
    html = render(month_sub, p1total, cumu_sub, cumutotal, part2, args.month)

    os.makedirs(args.outdir, exist_ok=True)
    stem = (args.code + "_" if args.code else "") + "當月帳目_" + args.month.replace("/", "")
    out = os.path.join(args.outdir, stem + ".html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"✓ 已輸出 HTML：{out}")
    print(f"  彙整：當月總計 {f(p1total)}；累計(開帳~當月) 總計 {f(cumutotal)}")
    print(f"  明細(--scope {args.scope}) 總計 {f(p2total)}；共 {len(part2)} 個科目頁")
    print(f"  對帳通過（逐筆=廠商小計=科目小計=區塊小計=總計）")
    if args.pdf:
        pdf = to_pdf(out)
        print(f"✓ 已輸出列印用 PDF：{pdf} ← 印這份（A5、無頁首頁尾）")

if __name__ == "__main__":
    main()
