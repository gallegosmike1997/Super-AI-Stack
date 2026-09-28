from pathlib import Path
import re

h = Path("index.html").read_text(encoding="utf-8")
have = set(re.findall(r'id="([^"]+)"', h))
refs = set()
for s in re.findall(r'<script>(.*?)</script>', h, re.S):
    refs |= set(re.findall(r"\$('#([a-zA-Z0-9_-]+)'\)", s))
    refs |= set(re.findall(r"getElementById\(['\"]([a-zA-Z0-9_-]+)['\"]\)", s))
missing = sorted(r for r in refs if r not in have)
print("have:", len(have), "refs:", len(refs), "missing:", missing)
