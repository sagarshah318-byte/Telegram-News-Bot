import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USERNAME = "REDBOXINDIA"
STATE_PATH = "redbox-bot/state.json"

def get_json(url, headers=None, data=None):
    request = urllib.request.Request(url, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

def x_get(url):
    return get_json(url, {"Authorization": "Bearer " + os.environ["X_BEARER_TOKEN"]})

def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": message,
        "disable_web_page_preview": "false",
    }).encode()
    result = get_json(url, {"Content-Type": "application/x-www-form-urlencoded"}, data)
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message: " + str(result))

def save_cursor(post_id):
    with open(STATE_PATH, "w", encoding="utf-8") as file:
        json.dump({"last_id": str(post_id)}, file, indent=2)
        file.write("\n")
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", STATE_PATH], check=True)
    subprocess.run(["git", "commit", "-m", "Save delivered X post cursor"], check=True)
    subprocess.run(["git", "push"], check=True)

def fetch_posts(user_id, since_id=None):
    posts = []
    page = None
    while True:
        params = {"max_results": "100", "tweet.fields": "created_at"}
        if since_id:
            params["since_id"] = str(since_id)
        if page:
            params["pagination_token"] = page
        url = "https://api.x.com/2/users/" + user_id + "/tweets?" + urllib.parse.urlencode(params)
        response = x_get(url)
        posts.extend(response.get("data", []))
        page = response.get("meta", {}).get("next_token")
        if not page:
            break
    return sorted(posts, key=lambda item: int(item["id"]))

def main():
    with open(STATE_PATH, encoding="utf-8") as file:
        state = json.load(file)
    user = x_get("https://api.x.com/2/users/by/username/" + USERNAME).get("data")
    if not user or not user.get("id"):
        raise RuntimeError("Unable to look up X account @" + USERNAME)
    print("Monitoring @" + USERNAME + " (user ID " + user["id"] + ")")

    last_id = state.get("last_id")
    if not last_id:
        recent = fetch_posts(user["id"])
        if recent:
            save_cursor(recent[-1]["id"])
            print("Starting cursor saved. New posts will be forwarded from now on.")
        else:
            print("No posts found yet; will try again next run.")
        return

    posts = fetch_posts(user["id"], last_id)
    print("New posts found: " + str(len(posts)))
    for post in posts:
        post_id = post["id"]
        link = "https://x.com/" + USERNAME + "/status/" + post_id
        message = "@" + USERNAME + " posted:\n\n" + post.get("text", "") + "\n\n" + link
        send_telegram(message)
        save_cursor(post_id)
        print("Forwarded post " + post_id)
        time.sleep(0.4)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        raise
