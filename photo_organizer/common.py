#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共用工具:檔案掃描、EXIF 拍攝時間、時間偏移量解析。"""

import re
from datetime import datetime
from pathlib import Path

IMG_EXTS = {'.jpg', '.jpeg', '.png', '.heic', '.heif'}
RAW_EXTS = {'.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.dng', '.raw'}

SOURCE_LABELS = {'camera': '相機', 'iphone': 'iPhone'}

EXIF_DATETIME_ORIGINAL = 36867  # DateTimeOriginal(位於 Exif IFD)
EXIF_IFD_POINTER = 0x8769
EXIF_DATETIME = 306             # 修改時間,備援用


def register_heif():
    """註冊 HEIC/HEIF 讀取器;回傳是否成功。"""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        return True
    except ImportError:
        return False


def parse_offset(text):
    """把偏移量字串轉成秒數。

    支援:純秒數 '90' / '-90',或時分秒 '+0:03:20' / '-1:02:03'。
    """
    if text is None:
        return 0
    t = str(text).strip()
    if not t:
        return 0
    sign = 1
    if t[0] in '+-':
        if t[0] == '-':
            sign = -1
        t = t[1:]
    if ':' in t:
        parts = t.split(':')
        if len(parts) > 3 or not all(re.fullmatch(r'\d+', p) for p in parts):
            raise ValueError(f'無法解析偏移量:{text!r}(格式:秒數 或 [H:]MM:SS)')
        nums = [int(p) for p in parts]
        while len(nums) < 3:
            nums.insert(0, 0)
        h, m, s = nums
        return sign * (h * 3600 + m * 60 + s)
    if not re.fullmatch(r'\d+', t):
        raise ValueError(f'無法解析偏移量:{text!r}(格式:秒數 或 [H:]MM:SS)')
    return sign * int(t)


def format_offset(seconds):
    sign = '-' if seconds < 0 else '+'
    s = abs(int(seconds))
    return f'{sign}{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}'


def read_taken(path):
    """讀取拍攝時間。回傳 (datetime, 'exif'|'mtime')。"""
    from PIL import Image
    path = Path(path)
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            val = None
            try:
                val = ex.get_ifd(EXIF_IFD_POINTER).get(EXIF_DATETIME_ORIGINAL)
            except Exception:
                pass
            if not val:
                val = ex.get(EXIF_DATETIME)
            if val:
                try:
                    return datetime.strptime(str(val).strip(), '%Y:%m:%d %H:%M:%S'), 'exif'
                except ValueError:
                    pass
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime), 'mtime'


def scan_source(root, source):
    """掃描單一來源資料夾(遞迴)。

    回傳 (photos, raw_orphans):
      photos: [{name, source, orig_path, raw_paths, taken, time_source}]
      raw_orphans: 找不到同名影像檔可配對的 RAW 檔路徑清單
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f'來源資料夾不存在:{root}')
    imgs = []
    raws = {}
    for p in sorted(root.rglob('*')):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in IMG_EXTS:
            imgs.append(p)
        elif ext in RAW_EXTS:
            raws.setdefault(p.stem.lower(), []).append(p)

    photos = []
    paired = set()
    for p in imgs:
        raw_list = raws.get(p.stem.lower(), [])
        if raw_list:
            paired.add(p.stem.lower())
        taken, tsrc = read_taken(p)
        photos.append({
            'name': p.name,
            'source': source,
            'orig_path': str(p.resolve()),
            'raw_paths': [str(r.resolve()) for r in raw_list],
            'taken': taken.isoformat(),
            'time_source': tsrc,
        })
    orphans = [str(p.resolve()) for stem, ps in raws.items() if stem not in paired for p in ps]
    return photos, orphans


def sanitize_name(name, fallback='未命名'):
    """把場景名 / 資料夾名中不適合當檔名的字元換成底線。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', str(name)).strip().strip('.')
    return cleaned or fallback


def unique_path(path):
    """若目標已存在,附加 _1、_2… 直到不衝突。"""
    path = Path(path)
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10000):
        cand = path.with_name(f'{stem}_{i}{suffix}')
        if not cand.exists():
            return cand
    raise RuntimeError(f'無法產生不重複的檔名:{path}')
