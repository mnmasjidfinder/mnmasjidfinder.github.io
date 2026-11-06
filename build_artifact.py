#!/usr/bin/env python3
"""Turn index.html + data/masajid.js into one self-contained file for a claude.ai artifact.

The artifact host adds its own <!doctype>/<head>/<body>, so this keeps only the
<title>, metas, <style> and body content, and inlines the data script.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
html = (ROOT / "index.html").read_text()
data = (ROOT / "data" / "masajid.js").read_text().replace("</", "<\\/")

head = re.search(r"<head>(.*?)</head>", html, re.S)[1]
body = re.search(r"<body>(.*?)</body>", html, re.S)[1]
head = re.sub(r'<meta (charset|name="viewport")[^>]*>\s*', "", head)
body = body.replace('<script src="data/masajid.js"></script>', f"<script>{data}</script>")

out = ROOT / "dist" / "masjid-finder.html"
out.parent.mkdir(exist_ok=True)
out.write_text(head.strip() + "\n" + body.strip() + "\n")
print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
