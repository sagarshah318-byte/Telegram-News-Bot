#!/usr/bin/env python3
"""Telegram mutual-fund lookup bot: Regular Plan - Growth only."""
import os, re, json, time, urllib.parse, urllib.request, datetime, math
from html import unescape

TOKEN = os.environ["MF_BOT_TOKEN"]
CHAT_ID = str(os.environ["MF_CHAT_ID"])
BASE = f"https://api.telegram.org/bot{TOKEN}/"
AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
STATE_PATH = "mf-bot/state.json"

def get(url, timeout=35):
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 (compatible; MFResearchBot/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def tg(method, payload):
    req = urllib.request.Request(BASE + method, data=urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type":"application/x-www-form-urlencoded"})
    return json.loads(get_request(req))

def get_request(req):
    with urllib.request.urlopen(req, timeout=35) as r:
        return r.read()

def send(text):
    # Telegram HTML formatting, split conservatively.
    for i in range(0, len(text), 3900):
        tg("sendMessage", {"chat_id": CHAT_ID, "text": text[i:i+3900], "parse_mode":"HTML", "disable_web_page_preview":"true"})

def clean(s):
    return re.sub(r"\s+", " ", unescape(s or "")).strip()

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f: return json.load(f)
    except Exception: return {"offset": 0}

def save_state(state):
    os.makedirs("mf-bot", exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f: json.dump(state, f, indent=2)

def parse_amfi():
    raw = get(AMFI_URL).decode("utf-8", "replace")
    rows=[]
    for line in raw.splitlines():
        p=line.split(";")
        if len(p) >= 6 and p[0].strip().isdigit():
            rows.append({"code":p[0].strip(),"name":clean(p[1]),"isin1":p[2].strip(),
                         "isin2":p[3].strip(),"nav":p[4].strip(),"date":p[5].strip()})
    return rows

def choose_scheme(query, rows):
    q=clean(query).lower()
    candidates=[r for r in rows if "regular" in r["name"].lower() and "growth" in r["name"].lower()
                and q in r["name"].lower()]
    if not candidates:
        words=[w for w in re.findall(r"[a-z0-9]+",q) if len(w)>2]
        candidates=[r for r in rows if "regular" in r["name"].lower() and "growth" in r["name"].lower()
                    and words and all(w in r["name"].lower() for w in words)]
    if not candidates:
        return None, []
    # prefer exact substring and unique match
    candidates.sort(key=lambda r:(0 if q in r["name"].lower() else 1, len(r["name"])))
    return (candidates[0] if len(candidates)==1 or q in candidates[0]["name"].lower() else None), candidates[:8]

def history(code):
    url=f"https://api.mfapi.in/mf/{urllib.parse.quote(code)}/latest"
    latest=json.loads(get(url))
    # API history endpoint returns all NAV observations
    full=json.loads(get(f"https://api.mfapi.in/mf/{urllib.parse.quote(code)}"))
    return full.get("data",[])

def cagr_returns(data):
    parsed=[]
    for x in data:
        try:
            d=datetime.datetime.strptime(x["date"], "%d-%m-%Y").date()
            v=float(x["nav"])
            if v>0: parsed.append((d,v))
        except Exception: pass
    parsed.sort()
    if not parsed: return {}, None
    end_date,end_nav=parsed[-1]
    out={}
    for years in (3,5,7,10):
        target=end_date-datetime.timedelta(days=round(years*365.2425))
        eligible=[(d,v) for d,v in parsed if d<=target]
        if eligible:
            start_date,start_nav=eligible[-1]
            actual=(end_date-start_date).days/365.2425
            out[years]=((end_nav/start_nav)**(1/actual)-1)*100
    return out, end_date

def fmt(v, digits=2):
    try: return f"{float(v):,.{digits}f}"
    except Exception: return "Not available"

def scheme_reply(query, rows):
    scheme, matches=choose_scheme(query, rows)
    if not scheme:
        if matches:
            opts="\n".join("• "+m["name"] for m in matches)
            return "I found multiple possible Regular Growth schemes. Please send the exact name:\n"+opts
        return "I couldn't find that scheme in AMFI's latest NAV file. Please check the scheme name and try again."
    navdata=history(scheme["code"])
    returns,end_date=cagr_returns(navdata)
    nav_date=scheme["date"]
    msg=(f"📊 <b>{scheme['name']}</b>\n"
         f"<i>Regular Plan – Growth</i>\n\n"
         f"<b>1. NAV</b>\n₹{fmt(scheme['nav'],4)}  |  {nav_date}\n\n"
         f"<b>2. Trailing returns (annualised CAGR)</b>\n"
         f"<pre>Period   Scheme\n")
    for y in (3,5,7,10):
        msg += f"{y:<7} {fmt(returns.get(y),2) + '%' if y in returns else 'Not available'}\n"
    msg += "</pre>"
    msg += ("<b>3. Benchmark</b>\nBenchmark name and official benchmark returns will be added from the AMC factsheet feed. "
            "They are not inferred from a proxy index.\n\n"
            "<b>4. AUM</b>\nLatest AMC disclosure required.\n\n"
            "<b>5. Fund Manager</b>\nLatest AMC disclosure required.\n\n"
            "<b>6. Top 10 Holdings</b>\nLatest AMC portfolio disclosure required.\n\n"
            f"<i>Scheme NAV source: AMFI. NAV history checked through {end_date:%d-%b-%Y}.</i>")
    return msg

def main():
    state=load_state()
    offset=int(state.get("offset",0))
    updates=json.loads(get(BASE+"getUpdates?"+urllib.parse.urlencode({"offset":offset,"timeout":0}))).get("result",[])
    if not updates: return
    rows=parse_amfi()
    for u in updates:
        state["offset"]=max(state.get("offset",0),u["update_id"]+1)
        m=u.get("message") or u.get("edited_message") or {}
        chat=str((m.get("chat") or {}).get("id",""))
        text=clean(m.get("text",""))
        if chat != CHAT_ID or not text: continue
        if text.startswith("/start") or text.startswith("/help"):
            send("Send a mutual fund scheme name. I search <b>Regular Plan – Growth</b> only.\nExample: HDFC Mid Cap Fund")
        else:
            try: send(scheme_reply(text, rows))
            except Exception as e:
                print(f"Lookup failed for {text!r}: {type(e).__name__}: {e}")
                send("Sorry, I couldn't retrieve the scheme data just now. Please try again shortly.")
    save_state(state)

if __name__ == "__main__":
    main()
