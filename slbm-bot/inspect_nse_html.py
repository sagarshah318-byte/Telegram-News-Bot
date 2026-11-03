from html.parser import HTMLParser
from pathlib import Path
import re

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
        excerpt = re.sub(r"\\s+", " ", content[max(0,pos-180):pos+360])
        print(f"EXCERPT_{term.upper().replace(' ','_')}={excerpt}")
