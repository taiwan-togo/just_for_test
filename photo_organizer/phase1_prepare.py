#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 — 前置整備。

兩個子命令:

  scan   掃描兩個來源、讀 EXIF 拍攝時間,輸出「時間軸前後對照」讓使用者
         確認兩機時鐘是否有時差(不做任何轉檔)。

  build  套用使用者確認後的偏移量,合併時間軸、HEIC→JPG、產生縮圖、
         依拍攝間隔切「場景」草稿,並輸出 Phase 2 挑選介面(selector.html)。

範例:
  python phase1_prepare.py scan  --camera /photos/dslr --iphone /photos/iphone --work ./work
  python phase1_prepare.py build --work ./work --iphone-offset=-0:03:20 --gap-minutes 30
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from common import (SOURCE_LABELS, format_offset, parse_offset, register_heif,
                    scan_source)

THUMB_LONG_EDGE = 480
CONVERT_QUALITY = 92
THUMB_QUALITY = 82


def _load_json(path, what):
    path = Path(path)
    if not path.is_file():
        sys.exit(f'找不到{what}:{path}(請先跑 scan)')
    return json.loads(path.read_text(encoding='utf-8'))


def _fmt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


# ---------------------------------------------------------------- scan

def cmd_scan(args):
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    heif_ok = register_heif()

    all_photos = []
    orphan_report = {}
    for source, root in (('camera', args.camera), ('iphone', args.iphone)):
        photos, orphans = scan_source(root, source)
        if any(p['orig_path'].lower().endswith(('.heic', '.heif')) for p in photos) and not heif_ok:
            sys.exit('偵測到 HEIC 檔,但未安裝 pillow-heif。請先:pip install pillow-heif')
        all_photos.extend(photos)
        orphan_report[source] = orphans

    if not all_photos:
        sys.exit('兩個來源都沒有掃到照片,請確認路徑。')

    scan_data = {
        'scanned_at': datetime.now().isoformat(timespec='seconds'),
        'sources': {'camera': str(Path(args.camera).resolve()),
                    'iphone': str(Path(args.iphone).resolve())},
        'photos': all_photos,
        'raw_orphans': orphan_report,
    }
    (work / 'scan.json').write_text(
        json.dumps(scan_data, ensure_ascii=False, indent=1), encoding='utf-8')

    report = build_timeline_report(all_photos, orphan_report)
    (work / 'timeline_check.txt').write_text(report, encoding='utf-8')
    print(report)
    print(f'\n已寫入 {work / "scan.json"} 與 {work / "timeline_check.txt"}')
    print('確認/校正時差後,執行 build,例如:')
    print(f'  python {Path(__file__).name} build --work {work} --iphone-offset=-0:03:20')


def build_timeline_report(photos, orphan_report):
    lines = ['== 時間軸前後對照(尚未套用任何偏移量)==', '']
    by_src = {'camera': [], 'iphone': []}
    for p in photos:
        by_src[p['source']].append(p)
    for src, plist in by_src.items():
        label = SOURCE_LABELS[src]
        if not plist:
            lines.append(f'{label:<7}: 0 張')
            continue
        times = sorted(datetime.fromisoformat(p['taken']) for p in plist)
        n_mtime = sum(1 for p in plist if p['time_source'] == 'mtime')
        extra = f'(其中 {n_mtime} 張無 EXIF,改用檔案時間)' if n_mtime else ''
        lines.append(f'{label:<7}: {len(plist)} 張   {_fmt(times[0])} ~ {_fmt(times[-1])} {extra}')
    lines.append('')

    merged = sorted(photos, key=lambda p: (p['taken'], p['name']))
    boundaries = []
    for a, b in zip(merged, merged[1:]):
        if a['source'] != b['source']:
            boundaries.append((a, b))
    if boundaries:
        lines.append('-- 來源交替處相鄰照片對照(用來目視檢查兩機時鐘是否對齊)--')
        for a, b in boundaries[:20]:
            ta = datetime.fromisoformat(a['taken'])
            tb = datetime.fromisoformat(b['taken'])
            delta = tb - ta
            lines.append(f'  {_fmt(ta)} {SOURCE_LABELS[a["source"]]:<6} {a["name"]:<24} → '
                         f'{_fmt(tb)} {SOURCE_LABELS[b["source"]]:<6} {b["name"]:<24} (Δ {delta})')
        if len(boundaries) > 20:
            lines.append(f'  …(其餘 {len(boundaries) - 20} 處省略)')
    else:
        lines.append('(兩來源時間完全沒有交錯,若當天確實混拍,通常代表其中一台時鐘偏差很大)')
    lines.append('')
    for src, orphans in orphan_report.items():
        if orphans:
            lines.append(f'⚠ {SOURCE_LABELS[src]} 有 {len(orphans)} 個 RAW 檔找不到同名 JPG,'
                         f'不會進入挑選流程:')
            for o in orphans[:10]:
                lines.append(f'    {o}')
    lines.append('')
    lines.append('校正方式:若 iPhone 時鐘比相機「快」了 3 分 20 秒,build 時加 --iphone-offset=-0:03:20')
    lines.append('         (offset 會「加」到該來源的拍攝時間上;也可用 --camera-offset 校正相機)')
    return '\n'.join(lines)


# ---------------------------------------------------------------- build

def cmd_build(args):
    from PIL import Image, ImageOps

    work = Path(args.work)
    scan_data = _load_json(work / 'scan.json', 'scan.json')
    register_heif()

    cam_off = parse_offset(args.camera_offset)
    iph_off = parse_offset(args.iphone_offset)
    offsets = {'camera': cam_off, 'iphone': iph_off}
    print(f'套用偏移量:相機 {format_offset(cam_off)},iPhone {format_offset(iph_off)}')
    if cam_off == 0 and iph_off == 0:
        print('(未指定任何偏移量;若已確認兩機時鐘一致,可忽略此訊息)')

    photos = scan_data['photos']
    for p in photos:
        t = datetime.fromisoformat(p['taken']) + timedelta(seconds=offsets[p['source']])
        p['taken_corrected'] = t
    photos.sort(key=lambda p: (p['taken_corrected'], p['name']))

    # 依拍攝間隔切場景草稿
    gap = timedelta(minutes=args.gap_minutes)
    scenes = []
    for p in photos:
        if not scenes or p['taken_corrected'] - scenes[-1]['photos'][-1]['taken_corrected'] > gap:
            scenes.append({'key': f's{len(scenes) + 1:02d}', 'photos': []})
        scenes[-1]['photos'].append(p)

    conv_dir = work / 'converted'
    thumb_dir = work / 'thumbs'
    conv_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    manifest_photos = []
    errors = []
    total = len(photos)
    for idx, p in enumerate(photos):
        pid = f'p{idx + 1:04d}'
        src_path = Path(p['orig_path'])
        is_heic = src_path.suffix.lower() in ('.heic', '.heif')
        print(f'\r處理縮圖/轉檔 {idx + 1}/{total} …', end='', flush=True)
        try:
            with Image.open(src_path) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode not in ('RGB', 'L'):
                    im = im.convert('RGB')
                display_rel = None
                if is_heic:
                    out = conv_dir / f'{pid}_{src_path.stem}.jpg'
                    im.save(out, 'JPEG', quality=CONVERT_QUALITY)
                    display_rel = f'converted/{out.name}'
                thumb = im.copy()
                thumb.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE))
                thumb.save(thumb_dir / f'{pid}.jpg', 'JPEG', quality=THUMB_QUALITY)
        except Exception as e:
            errors.append(f'{src_path}: {e}')
            continue
        manifest_photos.append({
            'id': pid,
            'name': src_path.name,
            'source': p['source'],
            'orig_path': p['orig_path'],
            'raw_paths': p['raw_paths'],
            'taken': p['taken_corrected'].isoformat(timespec='seconds'),
            'time_source': p['time_source'],
            'thumb': f'thumbs/{pid}.jpg',
            'display': display_rel or src_path.resolve().as_uri(),
        })
    print()

    manifest_ids = {mp['orig_path'] for mp in manifest_photos}
    manifest_scenes = []
    order_map = {}
    for sc in scenes:
        kept = [p for p in sc['photos'] if p['orig_path'] in manifest_ids]
        if not kept:
            continue
        key = f's{len(manifest_scenes) + 1:02d}'
        start, end = kept[0]['taken_corrected'], kept[-1]['taken_corrected']
        manifest_scenes.append({
            'key': key,
            'name': f'場景 {len(manifest_scenes) + 1:02d}',
            'start': start.isoformat(timespec='seconds'),
            'end': end.isoformat(timespec='seconds'),
        })
        for i, p in enumerate(kept):
            order_map[p['orig_path']] = (key, i)
    for mp in manifest_photos:
        mp['scene'], mp['order'] = order_map[mp['orig_path']]

    manifest = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'gap_minutes': args.gap_minutes,
        'offsets': {k: format_offset(v) for k, v in offsets.items()},
        'scenes': manifest_scenes,
        'photos': manifest_photos,
    }
    (work / 'manifest.js').write_text(
        'window.PHOTO_MANIFEST = ' + json.dumps(manifest, ensure_ascii=False) + ';\n',
        encoding='utf-8')
    (work / 'config.json').write_text(json.dumps({
        'sources': scan_data['sources'],
        'offsets_seconds': offsets,
        'gap_minutes': args.gap_minutes,
        'built_at': manifest['generated_at'],
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    template = Path(__file__).resolve().parent / 'selector_template.html'
    shutil.copyfile(template, work / 'selector.html')

    print(f'完成:{len(manifest_photos)} 張照片、{len(manifest_scenes)} 個場景草稿'
          f'(間隔 > {args.gap_minutes} 分鐘即切段)')
    if errors:
        print(f'⚠ 有 {len(errors)} 個檔案處理失敗,已略過:')
        for e in errors[:10]:
            print(f'    {e}')
    print(f'\nPhase 2:用瀏覽器開啟 {work / "selector.html"} 進行挑選,'
          f'完成後匯出 decisions.json 存回 {work}/')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    sp = sub.add_parser('scan', help='掃描來源並輸出時間軸對照(確認時差用)')
    sp.add_argument('--camera', required=True, help='專業相機來源資料夾')
    sp.add_argument('--iphone', required=True, help='iPhone 來源資料夾')
    sp.add_argument('--work', default='./work', help='工作目錄(預設 ./work)')
    sp.set_defaults(func=cmd_scan)

    bp = sub.add_parser('build', help='套用偏移量、轉檔、縮圖、切場景、產生挑選介面')
    bp.add_argument('--work', default='./work', help='工作目錄(預設 ./work)')
    bp.add_argument('--camera-offset', default='0',
                    help='加到相機拍攝時間的偏移量,例 -0:03:20 或 -200(秒)')
    bp.add_argument('--iphone-offset', default='0',
                    help='加到 iPhone 拍攝時間的偏移量,例 -0:03:20 或 -200(秒)')
    bp.add_argument('--gap-minutes', type=float, default=30,
                    help='切場景的拍攝間隔門檻,分鐘(預設 30)')
    bp.set_defaults(func=cmd_build)

    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
