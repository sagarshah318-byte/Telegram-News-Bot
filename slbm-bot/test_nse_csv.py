import csv
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://www.nseindia.com/market-data/securities-lending-and-borrowing"
OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        )
        page = context.new_page()
        response = page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        print(f"PAGE_HTTP_STATUS={response.status if response else 'unknown'}")
        page.wait_for_timeout(12000)

        # Record the visible filter controls so we can map NSE's live Series/month
        # selectors accurately before building the recurring alert logic.
        selects = page.locator("select")
        print(f"SELECT_COUNT={selects.count()}")
        for i in range(selects.count()):
            sel = selects.nth(i)
            try:
                print(f"SELECT_{i}_LABEL={sel.locator('option').all_text_contents()}")
                print(f"SELECT_{i}_VALUE={sel.input_value()}")
            except Exception as exc:
                print(f"SELECT_{i}_INSPECT_ERROR={exc}")

        table_rows = page.locator("table tr")
        print(f"VISIBLE_TABLE_ROW_COUNT={table_rows.count()}")
        for i in range(min(table_rows.count(), 8)):
            row_text = re.sub(r"\\s+", " ", table_rows.nth(i).inner_text()).strip()
            print(f"TABLE_ROW_{i}={row_text}")

        # Use NSE's own on-page CSV download control. Do not call hidden/private APIs.
        download_control = page.get_by_text("Download (.csv)", exact=True)
        if download_control.count() == 0:
            raise RuntimeError("NSE's visible Download (.csv) control was not found.")
        with page.expect_download(timeout=60000) as download_info:
            download_control.first.click()
        download = download_info.value
        suggested = download.suggested_filename or "nse_slbm_market_watch.csv"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", suggested)
        target = OUT / safe_name
        download.save_as(target)

        if not target.exists() or target.stat().st_size == 0:
            raise RuntimeError("NSE download completed but the CSV file is empty.")

        print(f"CSV_FILE={target}")
        print(f"CSV_SIZE_BYTES={target.stat().st_size}")
        raw = target.read_text(encoding="utf-8-sig", errors="replace")
        print("CSV_PREVIEW_BEGIN")
        print("\\n".join(raw.splitlines()[:12]))
        print("CSV_PREVIEW_END")

        # CSV may use a quoted multi-row heading; report parsed header candidates.
        try:
            with target.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            print(f"CSV_PARSED_ROW_COUNT={len(rows)}")
            for i, row in enumerate(rows[:5]):
                print(f"CSV_ROW_{i}={row}")
        except Exception as exc:
            print(f"CSV_PARSE_ERROR={exc}")
            raise
        browser.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
