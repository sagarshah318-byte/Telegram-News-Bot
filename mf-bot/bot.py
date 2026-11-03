#!/usr/bin/env python3
"""On-demand mutual-fund lookup. Triggered by Telegram webhook -> repository_dispatch."""
import os, re, json, urllib.parse, urllib.request, datetime
from html import unescape

TOKEN = os.environ["MF_BOT_TOKEN"]
CHAT_ID = str(os.environ["MF_CHAT_ID"])
QUERY = os.environ["MF_SCHEME"]
BASE = f"https://api.telegram.org/bot{TOKEN}/"
AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

def fetch(url, timeout=45):
    req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 (compatible; MFResearchBot/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read()

def telegram_send(text, reply_markup=None):
    data={"chat_id":CHAT_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":"true"}
    if reply_markup: data["reply_markup"]=json.dumps(reply_markup,ensure_ascii=False)
    payload=urllib.parse.urlencode(data).encode()
    req=urllib.request.Request(BASE+"sendMessage",data=payload,headers={"Content-Type":"application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req,timeout=30) as r:
        result=json.loads(r.read())
    if not result.get("ok"): raise RuntimeError("Telegram sendMessage failed")

def clean(s): return re.sub(r"\s+"," ",unescape(s or "")).strip()
def fmt(v,n=2):
    try: return f"{float(v):,.{n}f}"
    except Exception: return "Not available"

def amfi_rows():
    raw=fetch(AMFI_URL).decode("utf-8","replace")
    out=[]
    for line in raw.splitlines():
        p=line.split(";")
        if len(p)>=6 and p[0].strip().isdigit():
            out.append({"code":p[0].strip(),"name":clean(p[1]),"nav":p[4].strip(),"date":p[5].strip()})
    return out

def find_scheme(rows,q):
    q=clean(q).lower()
    if q.isdigit():
        exact=[r for r in rows if r["code"]==q and "regular" in r["name"].lower() and "growth" in r["name"].lower()]
        return (exact[0],exact) if exact else (None,[])
    eligible=[r for r in rows if "regular" in r["name"].lower() and "growth" in r["name"].lower()]
    words=[w for w in re.findall(r"[a-z0-9]+",q) if len(w)>2]
    def matches_words(name):
        tokens=re.sub(r"[^a-z0-9]+"," ",name.lower()).split()
        return bool(words) and all(w in tokens for w in words)
    matches=[r for r in eligible if matches_words(r["name"])]
    if len(matches)==1: return matches[0],matches
    if len(matches)>1:
        matches.sort(key=lambda r: len(r["name"]))
        return None,matches[:8]
    try:
        found=json.loads(fetch("https://api.mfapi.in/mf/search?q="+urllib.parse.quote(q)))
        candidates=[]
        for item in found if isinstance(found,list) else []:
            name=clean(item.get("schemeName",""))
            if "regular" in name.lower() and "growth" in name.lower() and matches_words(name):
                candidates.append({"code":str(item.get("schemeCode","")),"name":name,"nav":"","date":""})
        if len(candidates)==1: return candidates[0],candidates
        return None,candidates[:8]
    except Exception as e:
        print("MFAPI search fallback failed:",repr(e))
        return None,[]

def trailing(code):
    data=json.loads(fetch(f"https://api.mfapi.in/mf/{urllib.parse.quote(code)}"))
    parsed=[]
    for x in data.get("data",[]):
        try:
            d=datetime.datetime.strptime(x["date"],"%d-%m-%Y").date()
            v=float(x["nav"])
            if v>0: parsed.append((d,v))
        except Exception: pass
    parsed.sort()
    if not parsed: return {},None
    endd,endv=parsed[-1]; out={}
    for years in (3,5,7,10):
        target=endd-datetime.timedelta(days=round(years*365.2425))
        eligible=[(d,v) for d,v in parsed if d<=target]
        if eligible:
            sd,sv=eligible[-1]
            actual=(endd-sd).days/365.2425
            out[years]=((endv/sv)**(1/actual)-1)*100
    return out,endd

def main():
    rows=amfi_rows()
    scheme,matches=find_scheme(rows,QUERY)
    if not scheme:
        if matches:
            body="I found multiple Regular Plan – Growth schemes. Please select the correct fund:"
            keyboard={"inline_keyboard":[[{"text":x["name"][:60],"callback_data":"MFSEL:"+x["code"]}] for x in matches[:8]]}
            telegram_send(body,keyboard); return
        body="I couldn't find this Regular Plan – Growth scheme in AMFI's latest NAV file. Please check the scheme name."
        telegram_send(body); return
    ret,endd=trailing(scheme["code"])
    lines=[f"📊 <b>{scheme['name']}</b>","<i>Regular Plan – Growth</i>","",
           f"<b>1. NAV</b>  ₹{fmt(scheme['nav'],4)}  |  {scheme['date']}","","<b>2. Trailing returns (annualised CAGR)</b>",
           "<pre>Period    Scheme    Benchmark"]
    for y in (3,5,7,10):
        val=f"{fmt(ret[y])}%" if y in ret else "N/A"
        lines.append(f"{y:<9}{val:<10}Pending")
    lines += ["</pre>","<b>3. Benchmark</b> Pending AMC benchmark disclosure",
              "<b>4. AUM</b> Pending latest AMC disclosure",
              "<b>5. Fund Manager</b> Pending latest AMC disclosure",
              "<b>6. Top 10 Holdings</b> Pending latest AMC portfolio disclosure","",
              f"<i>Scheme NAV: AMFI. Historical NAV through {endd:%d-%b-%Y}.</i>" if endd else "<i>AMFI NAV source.</i>"]
    telegram_send("\\n".join(lines))

if __name__=="__main__": main()
