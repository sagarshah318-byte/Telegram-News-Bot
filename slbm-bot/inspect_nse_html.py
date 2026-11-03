from html.parser import HTMLParser
from pathlib import Path
import re
import urllib.request

HTML = Path("artifacts/nse_landing.html")
if not HTML.exists():
    raise SystemExit("NSE landing HTML was not saved by the HTTP/1.1 diagnostic step.")

content = HTML.read_text(encoding="utf-8", errors="replace")

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.items.append(("SCRIPT", attrs["src"]))
        elif tag in ("a", "iframe", "form") and any(k in attrs for k in ("href", "src", "action")):
            value = attrs.get("href") or attrs.get("src") or attrs.get("action")
            self.items.append((tag.upper(), value))

parser = Links()
parser.feed(content)
print(f"HTML_BYTES={HTML.stat().st_size}")
print(f"HTML_TITLE={re.search(r'<title[^>]*>(.*?)</title>', content, re.I|re.S).group(1).strip() if re.search(r'<title[^>]*>(.*?)</title>', content, re.I|re.S) else 'not found'}")
print("PAGE_LINKS_AND_SCRIPTS_BEGIN")
for kind, value in parser.items:
    if any(term in value.lower() for term in ("slb", "market", "csv", "api", "security", "borrow", "lending")):
        print(f"{kind}={value}")
print("PAGE_LINKS_AND_SCRIPTS_END")

for term in ("Download (.csv)", "csv", "securities-lending", "market-watch", "api/", "spread", "best bid", "best offers"):
    hits = [m.start() for m in re.finditer(re.escape(term), content, re.I)]
    print(f"TERM_{term.upper().replace(' ','_')}_COUNT={len(hits)}")
    for pos in hits[:4]:
        excerpt = re.sub(r"\s+", " ", content[max(0,pos-180):pos+360])
        print(f"EXCERPT_{term.upper().replace(' ','_')}={excerpt}")

# Inspect the exact first-party SLB page script referenced by NSE's HTML.
script_url = "https://www.nseindia.com/dist/js/sections/live-market/liveMarketSLB.js?v=30102026"
request = urllib.request.Request(script_url, headers={
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.nseindia.com/market-data/securities-lending-and-borrowing",
    "Accept": "*/*",
})
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        js = response.read().decode("utf-8", errors="replace")
        print(f"SLB_JS_HTTP_STATUS={response.status}")
        print(f"SLB_JS_BYTES={len(js)}")
    Path("artifacts/liveMarketSLB.js").write_text(js, encoding="utf-8")
    patterns = [
        r"https?[^\\s\\\"']+",
        r"[^\\s\\\"']*(?:api|slb|csv|download|market)[^\\s\\\"']*",
    ]
    print("SLB_JS_ENDPOINT_CANDIDATES_BEGIN")
    seen = set()
    for pattern in patterns:
        for match in re.findall(pattern, js, re.I):
            value = match[:500]
            if value not in seen and any(t in value.lower() for t in ("api", "slb", "csv", "download", "market")):
                seen.add(value)
                print(value)
    print("SLB_JS_ENDPOINT_CANDIDATES_END")
    for term in ("dnbslb", "dwldcsv", "series", "month", "spread", "fetch(", "ajax", "url:"):
        positions = [m.start() for m in re.finditer(re.escape(term), js, re.I)]
        print(f"SLB_JS_TERM_{term.upper()}_COUNT={len(positions)}")
        for pos in positions[:3]:
            print(re.sub(r"\\s+", " ", js[max(0,pos-220):pos+500]))
except Exception as exc:
    print(f"SLB_JS_FETCH_ERROR={exc}")


# Probe NSE's official SLBM series master, selected Series B contracts, and CSV export.
from urllib.request import Request, urlopen
import json
import urllib.parse

BASE = "https://www.nseindia.com"
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": BASE + "/market-data/securities-lending-and-borrowing",
    "Accept": "application/json, text/plain, */*",
}

def nse_get(path):
    req = Request(BASE + path, headers=COMMON_HEADERS)
    with urlopen(req, timeout=45) as response:
        body = response.read()
        return response.status, response.headers.get("Content-Type", ""), body

try:
    status, ctype, body = nse_get("/api/live-analysis-slb-series-master")
    master = json.loads(body.decode("utf-8", errors="replace"))
    print(f"SLB_MASTER_HTTP={status} CONTENT_TYPE={ctype} BYTES={len(body)}")
    print("SLB_MASTER_TOP_KEYS=" + ",".join(master.keys()))
    data = master.get("data", {})
    print("SLB_MASTER_GROUPS=" + ",".join(data.keys()))
    series_b = data.get("Series B", [])
    print(f"SLB_SERIES_B_COUNT={len(series_b)}")
    for item in series_b:
        print("SLB_SERIES_B_ITEM=" + json.dumps(item, ensure_ascii=False))
    # Probe the first two available Series B contracts, which should represent
    # the nearest available months. Keep the actual keys returned by NSE.
    for item in series_b[:2]:
        key = str(item.get("key", ""))
        label = str(item.get("value", ""))
        if not key:
            continue
        path = "/api/live-analysis-slb?series=" + urllib.parse.quote(key, safe="") 
        status, ctype, body = nse_get(path)
        print(f"SLB_DATA_HTTP={status} SERIES_KEY={key} LABEL={label} CONTENT_TYPE={ctype} BYTES={len(body)}")
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
            rows = payload.get("data", [])
            print(f"SLB_DATA_ROWS={len(rows)} TIMESTAMP={payload.get('timestamp')}")
            if rows:
                print("SLB_DATA_FIRST_ROW=" + json.dumps(rows[0], ensure_ascii=False))
        except Exception:
            print("SLB_DATA_BODY_START=" + body[:500].decode("utf-8", errors="replace"))
        csv_path = path + "&csv=true"
        csv_status, csv_type, csv_body = nse_get(csv_path)
        print(f"SLB_CSV_HTTP={csv_status} SERIES_KEY={key} CONTENT_TYPE={csv_type} BYTES={len(csv_body)}")
        print("SLB_CSV_HEADER=" + csv_body[:350].decode("utf-8", errors="replace").replace("\\n", " | "))
except Exception as exc:
    print(f"SLB_API_PROBE_ERROR={type(exc).__name__}: {exc}")
