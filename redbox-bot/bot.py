import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USERNAME = "REDBOXINDIA"
STATE_PATH = "redbox-bot/state.json"
FEEDS = [
    "https://fixupx.com/" + USERNAME + "/feed.xml",
    "https://fxtwitter.com/" + USERNAME + "/feed.xml",
]
ATOM = "{http://www.w3.org/2005/Atom}"
STATUS_RE = re.compile(r"/status/(\d+)", re.IGNORECASE)


class PlainTextParser(HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def clean_post_text(value):
    value = html.unescape(value or "")
    parser = PlainTextParser()
    try:
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    except Exception:
        pass

    lines = []
    for line in value.replace("\r", "\n").split("\n"):
        line = re.sub(r"[\t\u00a0 ]+", " ", line).strip()
        if line:
            lines.append(line)

    # Collapse repeated blank lines while retaining paragraph breaks.
    return "\n\n".join(lines).strip()


def canonical_post_id(link, guid):
    for candidate in (link or "", guid or ""):
        match = STATUS_RE.search(candidate)
        if match:
            return "status:" + match.group(1)
    return (guid or link or "").strip()


def get_feed(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; RedboxTelegramBot/1.0)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
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
            published = (
                entry.findtext(ATOM + "published", default="")
                or entry.findtext(ATOM + "updated", default="")
            )
            content = entry.find(ATOM + "content")
            summary = entry.find(ATOM + "summary")
            raw_text = (
                "".join(content.itertext()) if content is not None
                else (summary.text or "" if summary is not None else title)
            )
            entries.append({
                "id": canonical_post_id(link, guid),
                "link": link or guid,
                "text": clean_post_text(raw_text),
                "published": published,
            })
    else:
        channel = root.find("channel")
        if channel is None:
            raise RuntimeError("RSS XML has no channel")
        for item in channel.findall("item"):
            link = item.findtext("link", default="")
            guid = item.findtext("guid", default=link)
            description = item.findtext("description", default="")
            title = item.findtext("title", default="")
            published = item.findtext("pubDate", default="")
            raw_text = description or title
            entries.append({
                "id": canonical_post_id(link, guid),
                "link": link or guid,
                "text": clean_post_text(raw_text),
                "published": published,
            })

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
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            "Telegram HTTP " + str(exc.code) + ": "
            + exc.read().decode("utf-8", errors="replace")
        ) from exc
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message: " + str(result))


def save_state(seen_ids):
    # Keep one canonical ID per X status, preventing cross-feed duplicates.
    unique_ids = list(dict.fromkeys(seen_ids))[-500:]
    with open(STATE_PATH, "w", encoding="utf-8") as file:
        json.dump({"seen_ids": unique_ids}, file, indent=2)
        file.write("\n")

    import subprocess
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ], check=True)
    subprocess.run(["git", "add", STATE_PATH], check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if changed.returncode == 1:
        subprocess.run(["git", "commit", "-m", "Update delivered RSS post history"], check=True)
        # Another workflow or a code update may have advanced main after checkout.
        # Merge both state histories and retry instead of failing after a Telegram send.
        for attempt in range(1, 4):
            pushed = subprocess.run(["git", "push"])
            if pushed.returncode == 0:
                break
            if attempt == 3:
                raise RuntimeError("Unable to push RSS history after 3 attempts")
            subprocess.run(["git", "fetch", "origin", "main"], check=True)
            local_ids = json.load(open(STATE_PATH, encoding="utf-8")).get("seen_ids", [])
            try:
                remote_raw = subprocess.check_output(
                    ["git", "show", "origin/main:" + STATE_PATH], text=True
                )
                remote_ids = json.loads(remote_raw).get("seen_ids", [])
            except subprocess.CalledProcessError:
                remote_ids = []
            merged_ids = list(dict.fromkeys(remote_ids + local_ids))[-500:]
            subprocess.run(["git", "reset", "--hard", "origin/main"], check=True)
            with open(STATE_PATH, "w", encoding="utf-8") as file:
                json.dump({"seen_ids": merged_ids}, file, indent=2)
                file.write("\n")
            subprocess.run(["git", "add", STATE_PATH], check=True)
            staged = subprocess.run(["git", "diff", "--cached", "--quiet"])
            if staged.returncode == 1:
                subprocess.run(
                    ["git", "commit", "-m", "Merge delivered RSS post history"],
                    check=True,
                )
            elif staged.returncode != 0:
                raise RuntimeError("Unable to inspect merged RSS history")
    elif changed.returncode != 0:
        raise RuntimeError("Unable to inspect staged state changes")


def parse_published(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main():
    with open(STATE_PATH, encoding="utf-8") as file:
        state = json.load(file)

    # Convert older URL-based IDs to stable X status IDs.
    seen = []
    for old_id in state.get("seen_ids", []):
        seen.append(canonical_post_id(old_id, old_id))
    seen = list(dict.fromkeys(seen))
    seen_set = set(seen)

    entries = fetch_entries()

    # Deduplicate the feed itself before any filtering or sending.
    unique_entries = []
    entry_ids = set()
    for entry in entries:
        entry_id = entry.get("id")
        if entry_id and entry_id not in entry_ids:
            entry_ids.add(entry_id)
            unique_entries.append(entry)
    entries = unique_entries

    test_from = os.environ.get("TEST_FROM", "").strip()
    test_to = os.environ.get("TEST_TO", "").strip()

    if test_from and test_to:
        start_at = datetime.fromisoformat(test_from).astimezone(timezone.utc)
        end_at = datetime.fromisoformat(test_to).astimezone(timezone.utc)
        matching = []
        for entry in entries:
            published = entry.get("published", "").strip()
            if not published or not entry.get("id"):
                continue
            try:
                published_at = parse_published(published)
                if start_at <= published_at <= end_at and entry["id"] not in seen_set:
                    matching.append(entry)
            except (TypeError, ValueError, OverflowError):
                print("Skipping entry with unrecognized publication date: " + published)

        matching.reverse()
        print(
            "Test window " + test_from + " to " + test_to + ": "
            + str(len(matching)) + " matching feed entries"
        )
        for entry in matching:
            message = entry.get("text", "").strip()
            if not message:
                print("Skipping empty post: " + entry.get("link", ""))
                continue
            send_telegram(message)
            seen.append(entry["id"])
            seen_set.add(entry["id"])
            print("Forwarded test-window entry: " + (entry.get("link") or entry["id"]))
            time.sleep(0.5)

        # Record only entries at or before the backfill end time. Later posts
        # remain eligible for the normal five-minute poll.
        for entry in entries:
            entry_id = entry.get("id")
            published = entry.get("published", "").strip()
            if not entry_id or not published:
                continue
            try:
                if parse_published(published) <= end_at and entry_id not in seen_set:
                    seen.append(entry_id)
                    seen_set.add(entry_id)
            except (TypeError, ValueError, OverflowError):
                continue
        save_state(seen)
        if not matching:
            print("No feed entries carried publication timestamps inside the requested hour.")
        return

    if not seen:
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id:
                seen.append(entry_id)
                seen_set.add(entry_id)
        save_state(seen)
        print("Initial RSS history saved. Waiting for new posts.")
        return

    new_entries = [
        entry for entry in entries
        if entry.get("id") and entry["id"] not in seen_set
    ]
    new_entries.reverse()
    print("New RSS entries: " + str(len(new_entries)))

    for entry in new_entries:
        message = entry.get("text", "").strip()
        if not message:
            print("Skipping empty post: " + entry.get("link", ""))
            seen.append(entry["id"])
            seen_set.add(entry["id"])
            save_state(seen)
            continue

        send_telegram(message)
        seen.append(entry["id"])
        seen_set.add(entry["id"])
        save_state(seen)
        print("Forwarded: " + (entry.get("link") or entry["id"]))
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        raise
