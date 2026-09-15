import re, html
from pathlib import Path

dump = Path("/tmp/canvas_dump")
out = []
for f in sorted(dump.glob("SAS_*_Page.html")):
    raw = f.read_text(encoding="utf-8", errors="replace")
    out.append(f"\n########## {f.name} ##########")
    # headings
    for tag in ("h1", "h2", "h3", "h4"):
        for m in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", raw, re.S):
            t = html.unescape(re.sub(r"<[^>]+>", " ", m)).strip()
            if t:
                out.append(f"  {tag.upper()}: {t[:110]}")
    # visible text nodes inside divs with class hints (panels/cards/stat)
    for m in re.findall(r'class="([^"]*)"[^>]*>([^<]{4,120})<', raw):
        cls, txt = m
        t = html.unescape(txt).strip()
        if t and not t.startswith(("{", "<", "/*")):
            out.append(f"  [{cls[:40]}] {t}")
Path("/tmp/canvas_dump/structure.txt").write_text("\n".join(out), encoding="utf-8")
print("wrote structure.txt", len(out), "lines")

# wired element IDs in current index.html script blocks
h = Path.home().joinpath("Super_AI_Stack/web/index.html").read_text(encoding="utf-8")
scripts = "".join(re.findall(r"<script>(.*?)</script>", h, re.S))
ids = sorted(set(re.findall(r"\$\('#([a-zA-Z0-9_-]+)'\)", scripts)))
print("JS-wired IDs:", " ".join(ids))
