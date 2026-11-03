import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://api.bseindia.com/BseIndiaAPI/api"
STATE_PATH = Path("redbox-bot/bse_price_band_state.json")
IST = ZoneInfo("Asia/Kolkata")
SUBJECT = "CHANGES IN PRICE BAND"


def bse_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    }


def fetch_notices(start_date, end_date):
    params = {
        "strTxtNoticeNo": "",
        "strTxtDate": start_date.strftime("%Y-%m-%d"),
        "strTxtTodate": end_date.strftime("%Y-%m-%d"),
        "strScripcode": "",
        "strDep": "",
        "strSegment": "Equity",
        "subject": SUBJECT,
        "category": "",
        "containgtext": "",
    }
    url = BASE + "/getDataAdvance_New/w?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=bse_headers())
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.loads(response.read().decode("utf-8-sig"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() == "table" and isinstance(value, list):
                return value
        # Some BSE responses wrap the table in another object.
        for value in data.values():
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key.lower() == "table" and isinstance(nested, list):
                        return nested
    raise RuntimeError("BSE API response did not contain a notice table")


def field(row, *names):
    wanted = {name.lower() for name in names}
    for key, value in row.items():
        if str(key).lower() in wanted and value not in (None, ""):
            return str(value).strip()
    return ""


def find_pdf_url(row):
    for value in row.values():
        if isinstance(value, str):
            match = re.search(r'https?://[^\s\"\']+\.pdf(?:\?[^\s\"\']*)?', value, re.I)
            if match:
                return match.group(0).replace("&amp;", "&")
    notice_no = field(row, "NoticeNo", "Notice No", "NoticeNumber", "Notice_No")
    if notice_no:
        safe_no = urllib.parse.quote(notice_no, safe="-")
        return f"https://www.bseindia.com/downloads/UploadDocs/Notices/{safe_no}/{safe_no}.pdf"
    return ""


def download_pdf(url, notice_no):
    request = urllib.request.Request(url, headers=bse_headers())
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
        final_url = response.geturl()
    if not payload.startswith(b"%PDF"):
        raise RuntimeError(f"Downloaded file is not a PDF for notice {notice_no}: {final_url}")
    return payload


def send_document(payload, filename, caption):
    boundary = "----RedboxBSEBoundary7MA4YWxkTrZu0gW"
    fields = {
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "caption": caption[:1000],
    }
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
    )
    body.extend(b"Content-Type: application/pdf\r\n\r\n")
    body.extend(payload)
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    url = "https://api.telegram.org/bot" + os.environ["TELEGRAM_BOT_TOKEN"] + "/sendDocument"
    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            "Telegram HTTP " + str(exc.code) + ": "
            + exc.read().decode("utf-8", errors="replace")
        ) from exc
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the PDF: " + str(result))


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ], check=True)
    subprocess.run(["git", "add", str(STATE_PATH)], check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if changed.returncode == 1:
        subprocess.run(["git", "commit", "-m", "Update BSE price band notice history"], check=True)
        subprocess.run(["git", "push"], check=True)
    elif changed.returncode != 0:
        raise RuntimeError("Unable to inspect staged BSE notice state")


def main():
    now = datetime.now(IST)
    start_date = (now - timedelta(days=7)).date()
    end_date = now.date()
    rows = fetch_notices(start_date, end_date)
    print(f"BSE returned {len(rows)} notice rows from {start_date} to {end_date}")

    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    else:
        state = {"sent_notice_numbers": []}
    sent = set(state.get("sent_notice_numbers", []))
    newly_sent = []

    matches = []
    for row in rows:
        subject = field(row, "Subject", "NoticeSubject", "subject")
        if subject.strip().casefold() != SUBJECT.casefold():
            continue
        notice_no = field(row, "NoticeNo", "Notice No", "NoticeNumber", "Notice_No")
        if notice_no and notice_no not in sent:
            matches.append((notice_no, row))

    # Oldest first, so multiple notices arrive in chronological order.
    matches.sort(key=lambda item: item[0])
    for notice_no, row in matches:
        pdf_url = find_pdf_url(row)
        if not pdf_url:
            raise RuntimeError(f"No PDF link found for BSE notice {notice_no}")
        payload = download_pdf(pdf_url, notice_no)
        notice_date = field(row, "NoticeDate", "Notice Date", "Date")
        caption = f"BSE | {SUBJECT}\nNotice No: {notice_no}"
        if notice_date:
            caption += f"\nNotice Date: {notice_date}"
        send_document(payload, f"BSE_{notice_no}.pdf", caption)
        newly_sent.append(notice_no)
        sent.add(notice_no)
        print(f"Sent BSE PDF notice {notice_no}")
    
    if newly_sent:
        state["sent_notice_numbers"] = sorted(sent)[-500:]
        save_state(state)
        print(f"Sent {len(newly_sent)} new BSE price band PDF(s).")
    else:
        print("No new CHANGES IN PRICE BAND notices to send today.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        raise
