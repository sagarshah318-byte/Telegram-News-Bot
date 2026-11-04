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

def _pdf_text(pdf_bytes):
    """Extract text from an official AMC factsheet PDF."""
    from pypdf import PdfReader
    import io
    reader=PdfReader(io.BytesIO(pdf_bytes))
    return clean(" ".join(page.extract_text() or "" for page in reader.pages))

def _amfi_amc_page(fund_house):
    """Find the AMC's factsheet destination through AMFI's central directory."""
    directory="https://www.amfiindia.com/online-center/download-factsheets"
    html=fetch(directory).decode("utf-8","replace")
    wanted=re.sub(r"[^a-z0-9]","",fund_house.lower().replace("mutual fund",""))
    for anchor in re.findall(r"<a\b[^>]*>.*?</a>",html,re.I|re.S):
        label=" ".join(re.findall(r"(?:alt|title)\s*=\s*['\"]([^'\"]+)",anchor,re.I))
        label += " " + re.sub(r"<[^>]+>"," ",anchor)
        normalized=re.sub(r"[^a-z0-9]","",label.lower().replace("mutual fund",""))
        if wanted and (wanted in normalized or normalized in wanted):
            href=re.search(r"href\s*=\s*['\"]([^'\"]+)",anchor,re.I)
            if href:
                return urllib.parse.urljoin(directory,unescape(href.group(1)))
    return ""

def _factsheet_pdf_links(page_url, scheme_name):
    """Resolve PDF links from an AMC factsheet landing page."""
    if not page_url:
        return []
    if page_url.lower().split("?")[0].endswith(".pdf"):
        return [page_url]
    try:
        html=fetch(page_url).decode("utf-8","replace")
    except Exception as e:
        print("AMC factsheet landing page failed:",repr(e))
        return []
    links=[]
    for href,label in re.findall(r"<a\b[^>]*href\s*=\s*['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",html,re.I|re.S):
        absolute=urllib.parse.urljoin(page_url,unescape(href))
        label=clean(re.sub(r"<[^>]+>"," ",label))
        candidate=(absolute+" "+label).lower()
        if ".pdf" in absolute.lower() or "factsheet" in candidate or "fact-sheet" in candidate or "monthly" in candidate:
            links.append(absolute)
    # Prefer a scheme-specific factsheet, then the newest/general monthly factsheet.
    tokens=[x for x in re.findall(r"[a-z0-9]+",scheme_name.lower()) if len(x)>3]
    def score(url):
        u=url.lower()
        return (sum(t in u for t in tokens), "factsheet" in u or "fact-sheet" in u, "monthly" in u)
    return sorted(dict.fromkeys(links),key=score,reverse=True)[:8]

def _parse_generic_factsheet(text, result, scheme_name):
    """Conservative parser for common AMC factsheet labels; unknown fields stay blank."""
    text=clean(text)
    # Restrict extraction to the selected scheme's nearby section where possible.
    name_tokens=[x for x in re.findall(r"[a-z0-9]+",scheme_name.lower()) if len(x)>3]
    lower=text.lower()
    positions=[lower.find(x) for x in name_tokens if lower.find(x)>=0]
    if positions:
        pos=min(positions)
        section=text[max(0,pos-1200):pos+10000]
    else:
        section=text
    def first(pattern, source=section):
        m=re.search(pattern,source,re.I)
        return clean(m.group(1)) if m else ""
    if not result.get("benchmark"):
        result["benchmark"]=first(r"(?:benchmark(?:\s+index)?|tier\s*1\s*benchmark)\s*[:\-]\s*([A-Za-z0-9 &().,'/+_-]{4,100})")
    if not result.get("aum"):
        aum=first(r"(?:scheme\s+)?(?:month[- ]end\s+)?AUM\s*[:\-]?\s*(?:Rs\.?|₹|INR)?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)")
        if aum: result["aum"]="₹"+aum+" Cr"
    if not result.get("managers"):
        manager=first(r"(?:fund\s+manager(?:s)?|name\s+of\s+fund\s+manager(?:s)?)\s*[:\-]\s*([A-Z][A-Za-z .,&'-]{2,100})")
        if manager: result["managers"]=manager
    # Benchmark returns are reported by each AMC in its factsheet. Capture only
    # clearly labelled rows and leave unsupported periods blank.
    for years in (3,5,7,10):
        if years in result.get("benchmark_returns",{}):
            continue
        patterns=[
            r"(?:returns?\s+for\s+last\s+)"+str(years)+r"\s+years?\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            r"(?:last\s+)?"+str(years)+r"\s+years?\s+([\d.]+)%?\s+([\d.]+)%?\s+([\d.]+)%?"
        ]
        for pattern in patterns:
            hit=re.search(pattern,section,re.I)
            if hit:
                result.setdefault("benchmark_returns",{})[years]=hit.group(3)+"%"
                break
    if not result.get("holdings"):
        m=re.search(r"(?:top\s+10\s+holdings|portfolio\s+holdings)(.*?)(?:sector\s+allocation|asset\s+allocation|industry\s+allocation|disclaimer|additional\s+information)",section,re.I)
        if m:
            block=m.group(1)
            parsed=[]
            for line in re.split(r"(?<=%)\s+",block):
                hit=re.search(r"([A-Za-z][A-Za-z0-9 .&,'()/-]{2,80}?)\s+(\d{1,2}(?:\.\d{1,2})?)\s*%",line)
                if hit:
                    parsed.append(clean(hit.group(1))+" "+hit.group(2)+"%")
            result["holdings"]=parsed[:10]
    return result

def _dsp_scheme_metadata(scheme_name, result):
    """DSP's scheme pages expose benchmark, AUM, portfolio and manager in page content."""
    slug=urllib.parse.quote(scheme_name.lower().replace(" - regular plan - growth","").replace("regular plan - growth","").strip().replace(" ","-"))
    # DSP's public scheme route uses a stable scheme slug; the Small Cap route is verified.
    if "small cap" in scheme_name.lower():
        url="https://www.dspim.com/invest/mutual-fund-schemes/equity-funds/small-cap-fund/dspmc-direct-growth"
    else:
        url="https://www.dspim.com/invest/mutual-fund-schemes/equity-funds/"+slug+"/"+slug+"-direct-growth"
    html=fetch(url).decode("utf-8","replace")
    page=clean(re.sub(r"<script\b.*?</script>|<style\b.*?</style>"," ",html,flags=re.I|re.S))
    def grab(pattern):
        m=re.search(pattern,page,re.I)
        return clean(m.group(1)) if m else ""
    result["benchmark"]=grab(r"Benchmark:\s*([^<]{3,100})") or result.get("benchmark","")
    aum=grab(r"Total AUM\s*₹?\s*([\d,]+(?:\.\d+)?)\s*crores")
    if aum: result["aum"]="₹"+aum+" Cr"
    manager=grab(r"Fund managers?:\s*(?:Image:\s*)?([A-Z][A-Za-z .'-]{2,60})")
    if manager: result["managers"]=manager
    holdings=[]
    # DSP scheme pages publish the portfolio table in the page body.
    m=re.search(r"Portfolio as of.{0,100}?Holdings\s*\|\s*Weight %(.{0,12000}?)(?:View All Holdings|Sectors Weight)",page,re.I)
    if m:
        for name,weight in re.findall(r"([A-Z][A-Za-z0-9 &.,'()-]{2,90}?)\s*\\|\s*(\d+(?:\.\d+)?)\s*%",m.group(1)):
            holdings.append(clean(name)+" "+weight+"%")
    if holdings: result["holdings"]=holdings[:10]
    result["source"]=url
    return result

def amc_metadata(scheme_name, code):
    """Get scheme metadata for every AMC and enrich it from AMFI-listed AMC factsheets."""
    scheme=scheme_metadata(code)
    result={"fund_house":scheme.get("fund_house",""), "scheme_category":scheme.get("scheme_category",""),
            "benchmark":"", "benchmark_returns":{}, "aum":"", "managers":"", "holdings":[],
            "source":"https://www.amfiindia.com/online-center/download-factsheets"}
    # AMC identity/category are scheme-level metadata and are available for all supported AMCs.
    if not result["fund_house"]:
        return result
    if "dsp" in result["fund_house"].lower():
        try:
            result=_dsp_scheme_metadata(scheme_name,result)
            print("DSP official scheme fields:",json.dumps({k:v for k,v in result.items() if k!="source"}))
            return result
        except Exception as e:
            print("DSP official scheme lookup failed:",repr(e))
    try:
        landing=_amfi_amc_page(result["fund_house"])
        if not landing:
            print("AMFI factsheet directory did not expose an AMC link:",result["fund_house"])
            return result
        for pdf_url in _factsheet_pdf_links(landing,scheme_name):
            try:
                text=_pdf_text(fetch(pdf_url))
                if not text:
                    continue
                parsed=_parse_generic_factsheet(text,result,scheme_name)
                result=parsed
                result["source"]=pdf_url
                # Stop only when the key scheme-level fields have been found.
                if result.get("benchmark") and (result.get("aum") or result.get("managers") or result.get("holdings")):
                    break
            except Exception as e:
                print("AMC factsheet PDF parse failed:",pdf_url,repr(e))
        print("AMC factsheet fields:",json.dumps({k:v for k,v in result.items() if k!="source"}))
    except Exception as e:
        print("AMC factsheet discovery failed:",result["fund_house"],repr(e))
    return result

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
    lines += ["</pre>",f"<b>AMC</b> {meta.get('fund_house') or 'Not identified'}",f"<b>3. Benchmark</b> {meta.get('benchmark') or 'Not yet verified from latest AMC factsheet'}",
              f"<b>4. AUM</b> {meta.get('aum') or 'Not yet verified from latest scheme disclosure'}",
              f"<b>5. Fund Manager</b> {meta.get('managers') or 'Not yet verified from latest AMC factsheet'}",
              "<b>6. Top 10 Holdings</b> "+(" | ".join(meta["holdings"]) if meta.get("holdings") else "Not yet verified from latest portfolio disclosure"),"",
              '<a href="https://www.amfiindia.com/online-center/download-factsheets">AMC factsheets</a> | <a href="https://www.amfiindia.com/online-center/portfolio-disclosure">Portfolio disclosures</a>',"",
              f"<i>Scheme NAV: AMFI. Historical NAV through {endd:%d-%b-%Y}.</i>" if endd else "<i>AMFI NAV source.</i>"]
    telegram_send("\n".join(lines))

if __name__=="__main__": main()
