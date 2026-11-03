import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://www.nseindia.com"
PAGE_URL = BASE + "/market-data/securities-lending-and-borrowing"
STATE_PATH = Path("slbm-bot/state.json")
THRESHOLD = 3.0
IST = ZoneInfo("Asia/Kolkata")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": PAGE_URL,
    "Accept": "application/json, text/plain, */*",
}


def nse_get(path):
    request = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
        content_type = response.headers.get("Content-Type", "")
        status = response.status
    if status != 200:
        raise RuntimeError(f"NSE returned HTTP {status} for {path}")
    return json.loads(payload.decode("utf-8", errors="replace"))


def number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def show(value, decimals=2):
    if value in (None, "", "-"):
        return "N/A"
    try:
        result = float(str(value).replace(",", "").strip())
        return f"{result:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def load_market():
    master = nse_get("/api/live-analysis-slb-series-master")
    groups = master.get("data")
    if not isinstance(groups, dict) or "Series B" not in groups:
        raise RuntimeError("NSE series master response did not contain Series B")
    series_b = groups["Series B"]
    if not isinstance(series_b, list) or len(series_b) < 2:
        raise RuntimeError("NSE did not return at least two Series B monthly contracts")

    selected = series_b[:2]
    all_rows = []
    current_active = set()
    for contract in selected:
        key = str(contract.get("key", "")).strip()
        label = str(contract.get("value", "")).strip()
        if not key or not label:
            raise RuntimeError(f"Invalid Series B contract returned by NSE: {contract}")

        path = "/api/live-analysis-slb?series=" + urllib.parse.quote(key, safe="")
        payload = nse_get(path)
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise RuntimeError(f"NSE SLBM response for {label} did not contain a data list")

        timestamp = str(payload.get("timestamp", "")).strip()
        print(f"NSE {label}: {len(rows)} rows, timestamp={timestamp or 'not supplied'}")
        for row in rows:
            symbol = str(row.get("symbol", "")).strip()
            if not symbol:
                continue
            spread_per = number(row.get("spreadPer"))
            if spread_per >= THRESHOLD:
                identity = f"{key}:{symbol}"
                current_active.add(identity)
                all_rows.append({
                    "identity": identity,
                    "symbol": symbol,
                    "month": label,
                    "spread_per": spread_per,
                    "bid_qty": row.get("buyOrderQty1"),
                    "bid_price": row.get("buyOrderPrice1"),
                    "offer_price": row.get("sellOrderPrice1"),
                    "offer_qty": row.get("sellQty1"),
                    "ltp": row.get("lastTradedPrice"),
                    "underlying": row.get("underLyingLtp"),
                    "futures": row.get("futuresLtp"),
                    "spread": row.get("spread"),
                    "timestamp": timestamp,
                })
    return all_rows, current_active


def load_state():
    if not STATE_PATH.exists():
        return {"active_opportunities": []}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state.get("active_opportunities"), list):
            raise ValueError("active_opportunities is not a list")
        return state
    except Exception as exc:
        raise RuntimeError(f"Could not read SLBM alert state safely: {exc}") from exc


def telegram_send(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Telegram HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected SLBM alert: {result}")


def format_alert(row):
    return (
        f"📊 NSE SLBM OPPORTUNITY | {row['symbol']}\n"
        f"Series B: {row['month']}\n"
        f"Spread: ₹{show(row['spread'])} ({row['spread_per']:.2f}%)\n"
        f"Best Bid: ₹{show(row['bid_price'])} | Qty: {show(row['bid_qty'], 0)}\n"
        f"Best Offer: ₹{show(row['offer_price'])} | Qty: {show(row['offer_qty'], 0)}\n"
        f"SLB LTP: ₹{show(row['ltp'])}\n"
        f"Underlying LTP: ₹{show(row['underlying'])}\n"
        f"Futures LTP: ₹{show(row['futures'])}\n"
        f"Data time: {row['timestamp'] or 'Not supplied'}"
    )


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ], check=True)
    subprocess.run(["git", "add", str(STATE_PATH)], check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if changed.returncode == 1:
        subprocess.run(["git", "commit", "-m", "Update SLBM opportunity alert state"], check=True)
        # Rebase safely if another independent bot updated the branch during this run.
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    elif changed.returncode != 0:
        raise RuntimeError("Unable to inspect staged SLBM state")


def main():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        print("Weekend in India. No SLBM scan required.")
        return

    opportunities, current_active = load_market()
    state = load_state()
    previous_active = set(state["active_opportunities"])
    fresh = [row for row in opportunities if row["identity"] not in previous_active]
    fresh.sort(key=lambda row: (-row["spread_per"], row["month"], row["symbol"]))

    print(f"Qualifying opportunities (Spread >= {THRESHOLD:.1f}%): {len(opportunities)}")
    print(f"New opportunities to alert: {len(fresh)}")

    send_batched_alerts(fresh)

    # Re-alert only after an opportunity drops below 3% and later qualifies again.
    state["active_opportunities"] = sorted(current_active)
    state["last_checked_ist"] = now.isoformat(timespec="seconds")
    state["threshold_percent"] = THRESHOLD
    save_state(state)
    print("SLBM scan completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("SLBM BOT ERROR: " + str(exc), file=sys.stderr)
        raise
