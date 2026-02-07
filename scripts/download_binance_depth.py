#!/usr/bin/env python3
"""批次下載 Binance Data Portal 的 Futures Depth / Book Snapshot 檔案。

用法範例：
    python scripts/download_binance_depth.py \
        --symbol BTCUSDT \
        --start 2020-11-11 \
        --end 2020-11-30 \
        --dataset depth \
        --output data/historical/depth_raw

資料來源說明：
- 官方入口: https://data.binance.vision/
- Futures (USDⓈ-M / COIN-M) 檔案路徑格式：
  data/futures/{market}/{frequency}/{dataset}/{symbol}/{file_name}.zip
  其中 frequency 可為 daily/weekly/monthly，目前腳本以 daily 為主。

注意：
- 官方 zip 檔通常很大，下載前請確保磁碟空間充足。
- 檔案若不存在會收到 404，腳本會記錄 miss 清單供後續重試。
- 下載後預設同時保留 zip 與解壓出的 csv，可用 --no-keep-zip 僅保留 csv。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
from typing import Iterable, List, Tuple
import zipfile

import requests

BASE_URL = "https://data.binance.vision"
DATASET_FOLDER = {
    "depth": "depth",
    "book": "bookDepth",
    "diff": "bookDepthSnapshot"  # 差分檔 (若官方提供)
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Binance Futures depth/book snapshots")
    parser.add_argument("--symbol", default="BTCUSDT", help="交易對 (預設: BTCUSDT)")
    parser.add_argument("--market", choices=["um", "cm"], default="um", help="um=USDⓈ-M, cm=COIN-M")
    parser.add_argument("--dataset", choices=["depth", "book", "diff"], default="depth",
                        help="下載哪種 dataset: depth(逐筆 diff)、book(完整快照)、diff(官方差分)")
    parser.add_argument("--start", required=True, help="開始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="結束日期 (YYYY-MM-DD)")
    parser.add_argument("--output", default="data/historical/depth_raw",
                        help="輸出資料夾 (預設: data/historical/depth_raw)")
    parser.add_argument("--frequency", choices=["daily", "monthly"], default="daily",
                        help="官方檔案頻率 (預設 daily)")
    parser.add_argument("--keep-zip", action="store_true", help="保留下載的 zip (預設會刪除)")
    parser.add_argument("--skip-existing", action="store_true", help="若 csv 已存在則跳過下載")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP 逾時秒數 (預設 60)")
    parser.add_argument("--max-retries", type=int, default=3, help="單檔案下載最大重試次數")
    return parser.parse_args()


def iter_dates(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)


def build_daily_path(symbol: str, dataset: str, market: str, target_date: dt.date) -> Tuple[str, str]:
    folder = DATASET_FOLDER[dataset]
    date_str = target_date.strftime("%Y-%m-%d")
    file_name = f"{symbol}-{folder}-{date_str}.zip"
    url_path = f"data/futures/{market}/daily/{folder}/{symbol}/{file_name}"
    return url_path, file_name


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def download_file(session: requests.Session, url: str, dest: Path, timeout: int, retries: int) -> bool:
    for attempt in range(1, retries + 1):
        try:
            with session.get(url, timeout=timeout, stream=True) as resp:
                if resp.status_code == 404:
                    return False
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if chunk:
                            fh.write(chunk)
            return True
        except requests.RequestException as exc:
            print(f"⚠️  下載失敗 {url} (attempt {attempt}/{retries}): {exc}")
    return False


def extract_zip(zip_path: Path, output_dir: Path) -> List[str]:
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            target_path = output_dir / member
            zf.extract(member, output_dir)
            extracted.append(str(target_path))
    return extracted


def main() -> None:
    args = parse_args()
    start_date = dt.datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = dt.datetime.strptime(args.end, "%Y-%m-%d").date()
    if start_date > end_date:
        raise SystemExit("start date must be earlier than end date")

    output_dir = Path(args.output)
    ensure_dir(output_dir)
    print(f"📁 下載輸出資料夾: {output_dir.resolve()}")

    downloads = 0
    misses = []
    skipped = 0

    with requests.Session() as session:
        for cur_date in iter_dates(start_date, end_date):
            url_path, file_name = build_daily_path(args.symbol.upper(), args.dataset, args.market, cur_date)
            url = f"{BASE_URL}/{url_path}"
            csv_name = file_name.replace(".zip", ".csv")
            csv_path = output_dir / csv_name
            zip_path = output_dir / file_name

            if args.skip_existing and csv_path.exists():
                print(f"⏩ 已存在，跳過: {csv_name}")
                skipped += 1
                continue

            print(f"⬇️  下載 {url}")
            ok = download_file(session, url, zip_path, args.timeout, args.max_retries)
            if not ok:
                print(f"❌ 找不到或下載失敗: {url}")
                misses.append(url)
                continue

            extracted_files = extract_zip(zip_path, output_dir)
            print(f"✅ 解壓完成: {', '.join(Path(f).name for f in extracted_files)}")
            downloads += 1

            if not args.keep_zip:
                zip_path.unlink(missing_ok=True)

    print("\n===== 摘要 =====")
    print(f"成功下載: {downloads} 天")
    print(f"跳過 (已有檔案): {skipped} 天")
    if misses:
        print(f"缺失/失敗: {len(misses)} 天，清單如下：")
        for url in misses:
            print(f"  - {url}")
        miss_log = output_dir / "missing_depth_urls.txt"
        with open(miss_log, "w") as fh:
            for url in misses:
                fh.write(url + "\n")
        print(f"已將缺失列表寫入: {miss_log}")
    else:
        print("無缺失檔案 🎉")


if __name__ == "__main__":
    main()
