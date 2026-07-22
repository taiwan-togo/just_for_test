#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 — 依 decisions.json 執行歸檔。

以人工挑選結果為最終依據:場景順序、場景內照片順序完全照 decisions.json,
程式不重新排序。

  - 保留照片 → {NAS}/{YYYY-MM-DD}_{地點}/{NN}_{場景名}/{順序}_{原檔名}
  - 淘汰照片 → {NAS}/{YYYY-MM-DD}_{地點}/_淘汰/{原檔名}(不直接刪除)
  - 未定照片 → 預設留在原地,列入報告(--undecided 可改成 keep / reject)
  - RAW 檔跟著同名 JPG 一起搬移
  - 結束後在目的資料夾輸出 report.md / report.json

範例:
  python phase3_archive.py --decisions ./work/decisions.json \
      --dest /mnt/nas/photos --location 台北動物園 --dry-run
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from common import sanitize_name, unique_path


def load_decisions(path):
    path = Path(path)
    if not path.is_file():
        sys.exit(f'找不到 decisions.json:{path}')
    data = json.loads(path.read_text(encoding='utf-8'))
    if 'photos' not in data or 'scenes' not in data:
        sys.exit('decisions.json 格式不正確(缺少 scenes / photos)')
    return data


def transfer(src, dst, copy_mode, dry_run, actions, errors, kind):
    src = Path(src)
    if not src.is_file():
        errors.append(f'來源檔不存在,略過:{src}')
        return
    dst = unique_path(dst)
    actions.append({'kind': kind, 'from': str(src), 'to': str(dst)})
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if copy_mode:
        shutil.copy2(src, dst)
    else:
        shutil.move(str(src), str(dst))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--decisions', required=True, help='Phase 2 匯出的 decisions.json')
    ap.add_argument('--dest', required=True, help='NAS 目的根目錄(需已掛載)')
    ap.add_argument('--location', required=True, help='地點,用於資料夾名 YYYY-MM-DD_[地點]')
    ap.add_argument('--date', help='覆蓋日期(YYYY-MM-DD);預設取保留照片中最早的拍攝日')
    ap.add_argument('--folder-name', help='完全自訂目的資料夾名(覆蓋 YYYY-MM-DD_地點 規則)')
    ap.add_argument('--copy', action='store_true', help='複製而非移動(預設為移動)')
    ap.add_argument('--undecided', choices=['skip', 'keep', 'reject'], default='skip',
                    help='未定照片的處理方式(預設 skip:留在原地並列入報告)')
    ap.add_argument('--dry-run', action='store_true', help='只列出將執行的動作,不實際搬檔')
    args = ap.parse_args()

    data = load_decisions(args.decisions)
    scenes = sorted(data['scenes'], key=lambda s: s['order'])
    photos = data['photos']

    for p in photos:
        if p['keep'] is None:
            p['keep'] = {'skip': None, 'keep': True, 'reject': False}[args.undecided]

    kept = [p for p in photos if p['keep'] is True]
    rejected = [p for p in photos if p['keep'] is False]
    undecided = [p for p in photos if p['keep'] is None]

    if args.date:
        date_str = args.date
    else:
        pool = kept or photos
        date_str = min(p['taken'] for p in pool)[:10]
    folder = args.folder_name or f'{date_str}_{sanitize_name(args.location)}'
    dest_root = Path(args.dest) / folder
    trash_dir = dest_root / '_淘汰'

    mode = '複製' if args.copy else '移動'
    print(f'目的資料夾:{dest_root}')
    print(f'保留 {len(kept)} 張、淘汰 {len(rejected)} 張、未定 {len(undecided)} 張;'
          f'模式:{mode}{"(dry-run,不會動任何檔案)" if args.dry_run else ""}')

    actions, errors = [], []
    scene_summary = []

    if not args.dry_run:
        dest_root.mkdir(parents=True, exist_ok=True)

    # 保留:依場景順序 → 場景內人工順序
    for si, sc in enumerate(scenes, start=1):
        sc_photos = sorted((p for p in kept if p['scene'] == sc['key']),
                           key=lambda p: p['order'])
        if not sc_photos:
            continue
        sc_dir = dest_root / f'{si:02d}_{sanitize_name(sc["name"])}'
        for seq, p in enumerate(sc_photos, start=1):
            orig = Path(p['orig_path'])
            transfer(orig, sc_dir / f'{seq:03d}_{orig.name}',
                     args.copy, args.dry_run, actions, errors, 'keep')
            for raw in p.get('raw_paths', []):
                transfer(raw, sc_dir / f'{seq:03d}_{Path(raw).name}',
                         args.copy, args.dry_run, actions, errors, 'keep-raw')
        scene_summary.append({'folder': sc_dir.name, 'name': sc['name'],
                              'count': len(sc_photos)})

    # 淘汰:移到 _淘汰,保留原檔名
    for p in rejected:
        orig = Path(p['orig_path'])
        transfer(orig, trash_dir / orig.name,
                 args.copy, args.dry_run, actions, errors, 'reject')
        for raw in p.get('raw_paths', []):
            transfer(raw, trash_dir / Path(raw).name,
                     args.copy, args.dry_run, actions, errors, 'reject-raw')

    # 報告
    now = datetime.now().isoformat(timespec='seconds')
    star_dist = {}
    for p in kept:
        star_dist[p.get('stars', 0)] = star_dist.get(p.get('stars', 0), 0) + 1

    lines = [f'# 歸檔報告 — {folder}', '',
             f'- 執行時間:{now}',
             f'- 模式:{mode}{"(dry-run)" if args.dry_run else ""}',
             f'- decisions:{Path(args.decisions).resolve()}',
             f'- 保留 {len(kept)} 張 / 淘汰 {len(rejected)} 張 / 未定 {len(undecided)} 張',
             f'- 檔案動作共 {len(actions)} 筆(含 RAW),失敗 {len(errors)} 筆', '',
             '## 場景']
    for s in scene_summary:
        lines.append(f'- {s["folder"]}:{s["count"]} 張')
    if star_dist:
        lines += ['', '## 保留照片星等分布']
        for star in sorted(star_dist, reverse=True):
            label = '★' * star if star else '無星等'
            lines.append(f'- {label}:{star_dist[star]} 張')
    if undecided:
        lines += ['', f'## 未定照片(共 {len(undecided)} 張,未搬動)']
        lines += [f'- {p["orig_path"]}' for p in undecided]
    if errors:
        lines += ['', '## 錯誤']
        lines += [f'- {e}' for e in errors]
    report_md = '\n'.join(lines) + '\n'

    if args.dry_run:
        print('\n--- 預計執行的動作(前 40 筆)---')
        for a in actions[:40]:
            print(f'  [{a["kind"]:<10}] {a["from"]}\n               → {a["to"]}')
        if len(actions) > 40:
            print(f'  …(其餘 {len(actions) - 40} 筆省略)')
        print('\n--- 報告預覽 ---\n')
        print(report_md)
    else:
        md_path = unique_path(dest_root / 'report.md')
        md_path.write_text(report_md, encoding='utf-8')
        unique_path(dest_root / 'report.json').write_text(json.dumps({
            'executed_at': now, 'mode': mode, 'dry_run': False,
            'dest': str(dest_root), 'scenes': scene_summary,
            'kept': len(kept), 'rejected': len(rejected),
            'undecided': [p['orig_path'] for p in undecided],
            'actions': actions, 'errors': errors,
        }, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'\n完成。報告:{md_path}')
        if errors:
            print(f'⚠ 有 {len(errors)} 筆失敗,詳見報告。')
            sys.exit(1)


if __name__ == '__main__':
    main()
