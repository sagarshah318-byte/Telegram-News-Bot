import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

USER_ID = "1092883822473924608"
STATE_FILE = Path(__file__).with_name("state.json")
IST = ZoneInfo("Asia/Kolkata")


def request_json(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_posts(bearer, since_id):
    posts = []
    next_token = None
    while True:
        params = {"max_results": "100", "tweet.fields": "created_at,public_metrics,lang"}
        if since_id:
            params["since_id"] = since_id
        if next_token:
            params["pagination_token"] = next_token
        url = "https://api.x.com/2/users/" + USER_ID + "/tweets?" + urllib.parse.urlencode(params)
        payload = request_json(url, {"Authorization": f"Bearer {bearer}", "User-Agent": "RedboxTelegramBot/1.0"})
        if payload.get("errors"):
            raise RuntimeError(json.dumps(payload["errors"]))
        posts.extend(payload.get("data", []))
        next_token = payload.get("meta", {}).get("next_token")
        if not next_token:
            break
        time.sleep(0.25)
    return sorted(posts, key=lambda p: int(p["id"]))


def format_post(post):
    post_id = post["id"]
    text = html.escape(post.get("text", ""))
    created = post.get("created_at", "")
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(IST)
        created = dt.strftime("%d %b %Y, %I:%M %p IST")
    except Exception:
        pass
    url = f"https://x.com/REDBOXINDIA/status/{post_id}"
    body = f"<b>RedboxGlobal India · @REDBOXINDIA</b>\n{created}\n\n{text}\n\n<a href=\"{url}\">View on X</a>"
    if len(body) > 4000:
        body = body[:3950] + "…\n\n" + f'<a href="{url}">View full post on X</a>'
    return body


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    payload = request_json(url, data=data)
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram send failed: {payload}")


def main():
    bearer = os.environ.get("X_BEARER_TOKEN")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not all([bearer, tg_token, chat_id]):
        raise RuntimeError("Missing one or more GitHub Secrets: X_BEARER_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")

    state = {"since_id": None}
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))

    posts = fetch_posts(bearer, state.get("since_id"))
    if state.get("since_id") is None:
        if posts:
            state["since_id"] = max(posts, key=lambda p: int(p["id"]))["id"]
            STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            print(f"First run initialized at post {state['since_id']}; no older posts sent.")
        else:
            print("First run found no posts; waiting for the next new post.")
        return
    if not posts:
        print("No new Redbox posts.")
        return

    print(f"Found {len(posts)} new posts.")
    for post in posts:
        send_telegram(tg_token, chat_id, format_post(post))
        state["since_id"] = post["id"]
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        time.sleep(0.4)
    print(f"Delivered {len(posts)} posts. Latest ID: {state['since_id']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
