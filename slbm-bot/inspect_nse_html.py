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
