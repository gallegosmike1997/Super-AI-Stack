#!/usr/bin/env python3
"""Rewrite index.html script region: keep ONLY helpers block + chat block, insert NEW renderer."""
from pathlib import Path
import re

ROOT = Path("/home/kalimike/Super_AI_Stack")
INDEX = ROOT / "web/index.html"
WEB = ROOT / "web"

t = INDEX.read_text()
si = t.index("<script>")
body_close = t.index("</body>")
region = t[si:body_close]

# Split into (opentag, content) pairs.
chunks = re.split(r"(<script[^>]*>|<\/script>)", region)
blocks = []
i = 0
while i < len(chunks):
    if chunks[i].startswith("<script"):
        content = chunks[i + 1] if (i + 2 < len(chunks) and chunks[i + 2] == "</script>") else ""
        blocks.append(content)
        i += 3 if i + 2 < len(chunks) and chunks[i + 2] == "</script>" else 1
    else:
        i += 1

helpers = next((b for b in blocks if "const $=" in b and "addEventListener" not in b), None)
chat = next((b for b in blocks if "addEventListener('submit'" in b), None)
assert helpers and chat, f"helpers={bool(helpers)} chat={bool(chat)}"

new_block2 = (WEB / "_f_set.js").read_text().strip() + "\n" + \
             (WEB / "_f_diag.js").read_text().strip() + "\n" + \
             (WEB / "_f_act.js").read_text().strip() + "\n"

new_region = "<script>" + helpers + "</script>\n<script>" + new_block2 + "</script>\n<script>" + chat + "</script>\n"
new_text = t[:si] + new_region + t[body_close:]

assert "for(var i=1;i<9999" not in new_text
assert new_text.count("addEventListener('submit'") == 1
assert new_text.count("function renderDiag(") == 1
assert "renderActivity(" in new_text
INDEX.write_text(new_text)
print("OK: index.html = helpers + render + chat (3 blocks)")
print(f"  helpers: {len(helpers.splitlines())} lines")
print(f"  render : {len(new_block2.splitlines())} lines")
print(f"  chat   : {len(chat.splitlines())} lines")
print(f"  total  : {len(new_text.splitlines())} lines")


