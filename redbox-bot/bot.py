import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USERNAME = "REDBOXINDIA"
STATE_PATH = "redbox-bot/state.json"
FEEDS = [
    "https://rsshub.app/twitter/user/" + USERNAME,
    "https://rsshub.stsecurity.moe/twitter/user/" + USERNAME,
    "https://rsshub.axisnetcloud.com/twitter/user/" + USERNAME,
]
ATOM = "{http://www.w3.org/2005/Atom}"

def get_feed(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; RedboxTelegramBot/1.0)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            raw = response.read()
    except Exception as exc:
        raise RuntimeError("Feed request failed: " + str(exc)) from exc

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError("RSS endpoint did not return valid XML") from exc

    entries = []
    if root.tag.endswith("feed"):
        for entry in root.findall(ATOM + "entry"):
            title = entry.findtext(ATOM + "title", default="")
            link_node = entry.find(ATOM + "link")
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            guid = entry.findtext(ATOM + "id", default=link)
            published = entry.findtext(ATOM + "published", default="") or entry.findtext(ATOM + "updated", default="")
            content = entry.find(ATOM + "content")
            summary = entry.find(ATOM + "summary")
            text = "".join(content.itertext()) if content is not None else (summary.text or "" if summary is not None else title)
            entries.append({"id": guid or link, "link": link or guid, "title": title, "text": text, "published": published})
    else:
        channel = root.find("channel")
        if channel is None:
            raise RuntimeError("RSS XML has no channel")
        for item in channel.findall("item"):
            title = item.findtext("title", default="")
            link = item.findtext("link", default="")
            guid = item.findtext("guid", default=link)
            description = item.findtext("description", default="")
            pub_date = item.findtext("pubDate", default="")
            entries.append({"id": guid or link, "link": link or guid, "title": title, "text": description or title, "published": pub_date})

    if not entries:
        raise RuntimeError("Feed returned no entries")
    return entries

def fetch_entries():
    errors = []
    for feed_url in FEEDS:
        try:
            entries = get_feed(feed_url)
            print("RSS feed working: " + feed_url + " (" + str(len(entries)) + " items)")
            return entries
        except Exception as exc:
            errors.append(feed_url + " -> " + str(exc))
            print("Feed unavailable: " + feed_url + " -> " + str(exc))
    raise RuntimeError("All free RSS sources failed. " + " | ".join(errors))

def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": message[:4000],
        "disable_web_page_preview": "false",
    }).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Telegram HTTP " + str(exc.code) + ": " + exc.read().decode("utf-8", errors="replace")) from exc
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message: " + str(result))

def save_state(seen_ids):
    with open(STATE_PATH, "w", encoding="utf-8") as file:
        json.dump({"seen_ids": seen_ids[-500:]}, file, indent=2)
        file.write("\n")
    # Persist the cursor in the repository so the next scheduled runner sees it.
    import subprocess
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", STATE_PATH], check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if changed.returncode == 1:
        subprocess.run(["git", "commit", "-m", "Update delivered RSS post history"], check=True)
        subprocess.run(["git", "push"], check=True)
    elif changed.returncode != 0:
        raise RuntimeError("Unable to inspect staged state changes")

def main():
    with open(STATE_PATH, encoding="utf-8") as file:
        state = json.load(file)
    # Migrate from the earlier unused last_id field if present.
    seen = list(state.get("seen_ids", []))
    seen_set = set(seen)
    entries = fetch_entries()

    test_from = os.environ.get("TEST_FROM", "").strip()
    test_to = os.environ.get("TEST_TO", "").strip()

    if test_from and test_to:
        start_at = datetime.fromisoformat(test_from).astimezone(timezone.utc)
        end_at = datetime.fromisoformat(test_to).astimezone(timezone.utc)
        matching = []
        print("RSS publication-date samples:")
        for sample in entries[:8]:
            print("DATE=" + repr(sample.get("published", "")) + " TITLE=" + repr(sample.get("title", "")[:120]))
        for entry in entries:
            published = entry.get("published", "").strip()
            if not published or not entry.get("id"):
                continue
            try:
                try:
                    published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    published_at = parsedate_to_datetime(published)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                published_at = published_at.astimezone(timezone.utc)
                if start_at <= published_at <= end_at:
                    matching.append(entry)
            except (TypeError, ValueError, OverflowError):
                print("Skipping entry with unrecognized publication date: " + published)

        matching.reverse()
        print("Test window " + test_from + " to " + test_to + ": " + str(len(matching)) + " matching feed entries")
        for entry in matching:
            link = entry.get("link", "")
            title = entry.get("title", "").strip()
            body = entry.get("text", "").strip()
            message = "New post from @" + USERNAME + "\\n\\n"
            if title and title != body:
                message += title + "\\n\\n"
            message += body
            if link:
                message += "\\n\\n" + link
            send_telegram(message)
            seen.append(entry["id"])
            seen_set.add(entry["id"])
            print("Forwarded test-window entry: " + (link or entry["id"]))
            time.sleep(0.5)

        # Establish a clean baseline after the one-off test so older feed items
        # outside the requested hour are not sent by the next scheduled poll.
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id and entry_id not in seen_set:
                seen.append(entry_id)
                seen_set.add(entry_id)
        save_state(seen)
        if not matching:
            print("No feed entries carried publication timestamps inside the requested hour.")
        return

    # On the first normal run, record the current feed as the baseline without
    # sending an artificial connection message or replaying old posts.
    if not seen:
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id:
                seen.append(entry_id)
                seen_set.add(entry_id)
        save_state(seen)
        print("Initial RSS history saved. Waiting for new posts.")
        return

    new_entries = [entry for entry in entries if entry.get("id") and entry["id"] not in seen_set]
    new_entries.reverse()
    print("New RSS entries: " + str(len(new_entries)))

    for entry in new_entries:
        link = entry.get("link", "")
        title = entry.get("title", "").strip()
        body = entry.get("text", "").strip()
        message = "New post from @" + USERNAME + "\\n\\n"
        if title and title != body:
            message += title + "\\n\\n"
        message += body
        if link:
            message += "\\n\\n" + link
        send_telegram(message)
        seen.append(entry["id"])
        seen_set.add(entry["id"])
        save_state(seen)
        print("Forwarded: " + (link or entry["id"]))
        time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        raise
