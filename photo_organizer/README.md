# 照片整理工具(兩來源合併挑選流程)

把「專業相機(`DSC_xxxx.JPG` + RAW)」與「iPhone(`IMG_xxxx.HEIC`)」兩個來源的活動照片,
合併時間軸 → 人工挑選 → 歸檔到 NAS(資料夾規範 `YYYY-MM-DD_[地點]`)。

**人工挑選順序為最終依據**:Phase 3 完全照 `decisions.json` 的場景與順序執行,程式不重新排序。

## 安裝

```bash
pip install Pillow pillow-heif
```

## Phase 1 — 前置整備

### 1a. 掃描 + 時差對照

```bash
python phase1_prepare.py scan \
    --camera /path/to/dslr \
    --iphone /path/to/iphone \
    --work ./work
```

會遞迴掃描兩個資料夾、讀 EXIF 拍攝時間,輸出 `work/timeline_check.txt`:
兩來源的張數與時間範圍,以及「來源交替處」相鄰照片的時間對照,
用來目視檢查兩機時鐘是否有時差。RAW 檔會自動與同名 JPG 配對。

### 1b. 確認/校正偏移量後建置

```bash
python phase1_prepare.py build --work ./work \
    --iphone-offset=-0:03:20 \   # 例:iPhone 時鐘快了 3 分 20 秒
    --gap-minutes 30             # 場景切分間隔(預設 30 分鐘)
```

- 偏移量會「加」到該來源的拍攝時間;也有 `--camera-offset`。格式:`[H:]MM:SS` 或秒數。
- HEIC → JPG 存到 `work/converted/`;全部照片產生縮圖到 `work/thumbs/`。
- 依校正後時間合併排序,拍攝間隔超過門檻即切一個「場景」草稿。
- 產出 `work/manifest.js` + `work/selector.html`。

## Phase 2 — 挑選介面(本地 HTML)

用瀏覽器直接開 `work/selector.html`(離線可用,進度自動存在瀏覽器 localStorage):

- 縮圖牆按場景分區;**雙擊場景標題**重新命名;**「⇧ 併入上一場景」**合併場景。
- 快捷鍵:`←`/`→` 移動選取、`K`/`Space` 保留、`X` 刪除、`U` 未定、
  `1`–`5` 星等、`0` 清除星等、`Enter` 放大檢視、`Esc` 關閉。
- 場景內**拖曳縮圖**調整照片順序。
- 按「匯出 decisions.json」下載決定清單(含原檔路徑、場景、順序、去留、星等),
  **存回 `work/` 目錄**。

## Phase 3 — 執行歸檔

先 dry-run 檢查:

```bash
python phase3_archive.py \
    --decisions ./work/decisions.json \
    --dest /mnt/nas/photos \
    --location 台北動物園 \
    --dry-run
```

確認無誤後拿掉 `--dry-run` 正式執行:

- 保留照片 → `{dest}/{YYYY-MM-DD}_{地點}/{NN}_{場景名}/{順序}_{原檔名}`
  (日期預設取保留照片中最早的拍攝日,可用 `--date` 覆蓋)
- 淘汰照片 → 同資料夾下的 `_淘汰/`,**不直接刪除**
- 未定照片 → 預設留在原地並列入報告(`--undecided keep|reject` 可改)
- RAW 檔跟著同名 JPG 一起搬;檔名衝突自動加 `_1` 後綴
- 預設「移動」,`--copy` 改為複製
- 結束後在目的資料夾輸出 `report.md` / `report.json` 執行報告

## 工作目錄結構

```
work/
  scan.json            # 1a 掃描結果
  timeline_check.txt   # 時差對照(供人工確認)
  config.json          # 來源、偏移量、間隔設定
  converted/           # HEIC 轉出的 JPG
  thumbs/              # 全部縮圖
  manifest.js          # Phase 2 介面資料
  selector.html        # Phase 2 挑選介面
  decisions.json       # Phase 2 匯出(使用者存入)
```
