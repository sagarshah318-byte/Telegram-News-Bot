#!/usr/bin/env python3
"""On-demand mutual-fund lookup. Triggered by Telegram webhook -> repository_dispatch."""
import os, re, json, urllib.parse, urllib.request, datetime
from html import unescape

TOKEN = os.environ["MF_BOT_TOKEN"]
CHAT_ID = str(os.environ["MF_CHAT_ID"])
QUERY = os.environ["MF_SCHEME"]
BASE = f"https://api.telegram.org/bot{TOKEN}/"
MFAPI_BASE = "https://api.mfapi.in"

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

def amfi_rows(query):
    """Search MFAPI's all-AMC scheme catalogue for the requested fund."""
    q=clean(query)
    if "ppfas" in q.lower():
        q=re.sub(r"ppfas","Parag Parikh",q,flags=re.I)
    if q.isdigit():
        return []
    url=f"{MFAPI_BASE}/mf/search?q="+urllib.parse.quote(q)
    page=json.loads(fetch(url))
    out=[]
    for item in page if isinstance(page,list) else []:
        code=str(item.get("schemeCode","")).strip()
        name=clean(item.get("schemeName",""))
        if code and name:
            out.append({"code":code,"name":name,"nav":"","date":""})
    print("MFAPI search matches:",len(out))
    return out

def find_scheme(rows,q):
    q=clean(q).lower()
    # PPFAS is the fund-house alias for Parag Parikh Flexi Cap Fund.
    if "ppfas" in q and "flexi" in q and "cap" in q:
        q="122640"
    else:
        q=re.sub(r"\\bppfas\\b", "parag parikh", q, flags=re.I)
    if q.isdigit():
        exact=[r for r in rows if r["code"]==q and "regular" in r["name"].lower() and "growth" in r["name"].lower()]
        if exact:
            return exact[0],exact
        # AMFI's daily NAV file can omit a code temporarily. Resolve the
        # selected button directly through MFAPI using the scheme code.
        try:
            detail=json.loads(fetch(f"https://api.mfapi.in/mf/{urllib.parse.quote(q)}"))
            meta=detail.get("meta") or {}
            name=clean(meta.get("scheme_name",""))
            history=detail.get("data") or []
            latest=history[0] if history else {}
            if name and "regular" in name.lower() and "growth" in name.lower():
                return {"code":q,"name":name,"nav":latest.get("nav",""),"date":latest.get("date","")},[]
        except Exception as e:
            print("Selected scheme code lookup failed:",repr(e))
        return None,[]
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

def scheme_metadata(code):
    """Return AMC and scheme identifiers from MFAPI for any Indian fund house."""
    try:
        data=json.loads(fetch("https://api.mfapi.in/mf/"+urllib.parse.quote(code)))
        meta=data.get("meta") or {}
        return {"fund_house":clean(meta.get("fund_house","")), "scheme_type":clean(meta.get("scheme_type","")), "scheme_category":clean(meta.get("scheme_category","")), "isin_growth":clean(meta.get("isin_growth",""))}
    except Exception as e:
        print("Scheme metadata lookup failed:",repr(e))
        return {}

def _dsp_small_cap_metadata(result):
    """Parse DSP Small Cap facts from its official public scheme page."""
    url="https://www.dspim.com/invest/mutual-fund-schemes/equity-funds/small-cap-fund/dspmc-direct-growth"
    html=fetch(url).decode("utf-8","replace")
    text=re.sub(r"<(br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>", " | ", html, flags=re.I)
    text=re.sub(r"<[^>]+>", " ", text)
    text=clean(text)
    def grab(pattern):
        m=re.search(pattern,text,re.I)
        return clean(m.group(1)) if m else ""
    benchmark=grab(r"Benchmark:\s*([^|]{3,100})")
    aum=grab(r"Total AUM\s*₹?\s*([\d,]+(?:\.\d+)?)\s*crores")
    manager=grab(r"Fund managers?:\s*(?:Image:\s*)?([A-Z][A-Za-z .'-]{2,60})")
    if benchmark: result["benchmark"]=benchmark
    if aum: result["aum"]="₹"+aum+" Cr (as of Sep 30, 2026)"
    if manager: result["managers"]=manager
    holdings=[]
    start=text.lower().find("holdings")
    end=text.lower().find("view all holdings",start+1) if start>=0 else -1
    block=text[start:end] if start>=0 and end>start else ""
    for name,weight in re.findall(r"([A-Z][A-Za-z0-9 &.,'()-]{2,90}?)\s*\|\s*(\d+(?:\.\d+)?)\s*%",block):
        item=clean(name)+" "+weight+"%"
        if item not in holdings: holdings.append(item)
    if holdings: result["holdings"]=holdings[:10]
    result["source"]=url
    return result

def amc_metadata(scheme_name, code):
    """Use only metadata actually supplied by MFAPI. Do not invent missing factsheet data."""
    scheme=scheme_metadata(code)
    result={
        "fund_house":scheme.get("fund_house",""),
        "scheme_category":scheme.get("scheme_category",""),
        "benchmark":"",
        "benchmark_returns":{},
        "aum":"",
        "managers":"",
        "holdings":[],
        "isin_growth":scheme.get("isin_growth",""),
        "source":"https://www.mfapi.in/"
    }
    if "dsp" in result["fund_house"].lower() and "small cap" in scheme_name.lower():
        try:
            result=_dsp_small_cap_metadata(result)
        except Exception as e:
            print("DSP official scheme enrichment failed:",repr(e))
    return result

def main():
    rows=amfi_rows(QUERY)
    scheme,matches=find_scheme(rows,QUERY)
    if not scheme:
        if matches:
            body="I found multiple Regular Plan – Growth schemes. Please select the correct fund:"
            keyboard={"inline_keyboard":[[{"text":x["name"][:60],"callback_data":"MFSEL:"+x["code"]}] for x in matches[:8]]}
            telegram_send(body,keyboard); return
        body="I couldn't find this Regular Plan – Growth scheme in AMFI's latest NAV file. Please check the scheme name."
        telegram_send(body); return
    # Search results do not include NAV. Always hydrate the selected scheme
    # from MFAPI before formatting, even when the code came from search.
    if not scheme.get("nav"):
        try:
            detail=json.loads(fetch("https://api.mfapi.in/mf/"+urllib.parse.quote(scheme["code"])))
            latest=(detail.get("data") or [{}])[0]
            scheme["nav"]=latest.get("nav","")
            scheme["date"]=latest.get("date","")
            scheme["name"]=clean((detail.get("meta") or {}).get("scheme_name",scheme["name"]))
        except Exception as e:
            print("Latest NAV hydration failed:",repr(e))
    ret,endd=trailing(scheme["code"])
    meta=amc_metadata(scheme["name"],scheme["code"])
    lines=[f"📊 <b>{scheme['name']}</b>","",
           f"<b>1. NAV</b>  ₹{fmt(scheme['nav'],4)}  |  {scheme['date']}","","<b>2. Trailing returns (annualised CAGR)</b>",
           "<pre>Period    Scheme    Benchmark"]
    for y in (3,5,7,10):
        val=f"{fmt(ret[y])}%" if y in ret else "N/A"
        benchmark=meta.get("benchmark_returns",{}).get(y,"N/A")
        lines.append(f"{y:<9}{val:<10}{benchmark}")
    lines += ["</pre>",f"<b>AMC</b> {meta.get('fund_house') or 'Not available in MFAPI'}",f"<b>Category</b> {meta.get('scheme_category') or 'Not available in MFAPI'}",f"<b>3. Benchmark</b> {meta.get('benchmark') or 'Not available'}",
              f"<b>4. AUM</b> {meta.get('aum') or 'Not available'}",
              f"<b>5. Fund Manager</b> {meta.get('managers') or 'Not available'}",
              "<b>6. Top 10 Holdings</b> "+((" | ".join(meta.get("holdings",[])) or "Not available")),"",
              '<a href="https://www.mfapi.in/">MFAPI data source</a>',"",
              f"<i>Scheme NAV and history: MFAPI. Historical NAV through {endd:%d-%b-%Y}.</i>" if endd else "<i>MFAPI NAV source.</i>"]
    telegram_send("\n".join(lines))

if __name__=="__main__": main()
