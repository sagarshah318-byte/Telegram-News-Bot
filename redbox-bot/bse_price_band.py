import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

RSS_URL = "https://beta.bseindia.com/data/xml/notices.xml"
STATE_PATH = Path("redbox-bot/bse_price_band_state.json")
IST = ZoneInfo("Asia/Kolkata")
SUBJECT = "CHANGES IN PRICE BAND"


def bse_headers(referer="https://www.bseindia.com/"):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, text/html, application/pdf, */*",
        "Referer": referer,
        "Origin": "https://www.bseindia.com",
    }


def fetch_url(url, referer="https://www.bseindia.com/"):
    request = urllib.request.Request(url, headers=bse_headers(referer))
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(), response.geturl(), response.headers.get("Content-Type", "")


def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element, name):
    for child in element.iter():
        if local_name(child.tag) == name.lower() and child.text:
            return html.unescape(child.text.strip())
    return ""


def fetch_notices(start_date, end_date):
    payload, _, _ = fetch_url(RSS_URL)
    root = ET.fromstring(payload)
    items = [node for node in root.iter() if local_name(node.tag) in ("item", "entry")]
    notices = []

    for item in items:
        title = child_text(item, "title")
        if title.strip().casefold() != SUBJECT.casefold():
            continue

        link = ""
        for node in item.iter():
            if local_name(node.tag) == "link":
                link = (node.attrib.get("href") or node.text or "").strip()
                if link:
                    break
        link = html.unescape(link)
        if link:
            link = urljoin(RSS_URL, link)

        date_text = child_text(item, "pubdate") or child_text(item, "published") or child_text(item, "updated")
        notice_date = ""
        if date_text:
            try:
                parsed = parsedate_to_datetime(date_text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=IST)
                if not (start_date <= parsed.astimezone(IST).date() <= end_date):
                    continue
                notice_date = parsed.astimezone(IST).strftime("%d-%m-%Y")
            except (TypeError, ValueError, OverflowError):
                notice_date = date_text

        raw_text = " ".join((item.itertext()))
        notice_match = re.search(r"20\d{6}-\d+", raw_text + " " + link)
        notice_no = notice_match.group(0) if notice_match else ""
        if not notice_no:
            continue

        pdf_match = re.search(r'https?://[^\s<>"\']+\.pdf(?:\?[^\s<>"\']*)?', html.unescape(raw_text), re.I)
        pdf_url = pdf_match.group(0).replace("&amp;", "&") if pdf_match else ""
        notices.append({
            "Subject": title,
            "NoticeNo": notice_no,
            "NoticeDate": notice_date,
            "NoticeLink": link,
            "PdfUrl": pdf_url,
        })

    return notices


def field(row, *names):
    wanted = {name.lower() for name in names}
    for key, value in row.items():
        if str(key).lower() in wanted and value not in (None, ""):
            return str(value).strip()
    return ""


def find_pdf_url(row):
    direct = field(row, "PdfUrl", "PDF", "DocumentUrl")
    if direct and ".pdf" in direct.lower():
        return urljoin("https://www.bseindia.com/", direct)

    notice_link = field(row, "NoticeLink", "Link", "URL")
    if not notice_link:
        return ""

    if ".pdf" in notice_link.lower():
        return notice_link

    page, final_url, _ = fetch_url(notice_link, referer="https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx")
    page_text = html.unescape(page.decode("utf-8", errors="replace"))
    patterns = [
        r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s"\'<>]*)?',
        r'(?:href|src)\s*=\s*["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text, re.I)
        if match:
            candidate = match.group(1) if match.lastindex else match.group(0)
            return urljoin(final_url, candidate.replace("&amp;", "&"))
    raise RuntimeError(f"Could not locate the official PDF link on BSE notice page for {field(row, 'NoticeNo')}")


def download_pdf(url, notice_no):
    payload, final_url, content_type = fetch_url(url, referer="https://www.bseindia.com/")
    if not payload.startswith(b"%PDF"):
        raise RuntimeError(
            f"Downloaded content is not a PDF for notice {notice_no} "
            f"(content-type={content_type}, url={final_url})"
        )
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
    print(f"BSE RSS returned {len(rows)} matching price-band notice(s) from {start_date} to {end_date}")

    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    else:
        state = {"sent_notice_numbers": []}
    sent = set(state.get("sent_notice_numbers", []))
    newly_sent = []

    matches = []
    for row in rows:
        notice_no = field(row, "NoticeNo", "Notice No", "NoticeNumber", "Notice_No")
        if notice_no and notice_no not in sent:
            matches.append((notice_no, row))

    matches.sort(key=lambda item: item[0])
    for notice_no, row in matches:
        pdf_url = find_pdf_url(row)
        if not pdf_url:
            raise RuntimeError(f"No official PDF link found for BSE notice {notice_no}")
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
