#!/usr/bin/env python3
import re, subprocess, sys
from pathlib import Path
h = Path("/home/kalimike/Super_AI_Stack/web/index.html").read_text()
chunks = re.split(r"(<script[^>]*>|</script>)", h)
c = []
for i in range(len(chunks)):
    if chunks[i].startswith("<script") and i + 2 < len(chunks) and chunks[i+2] == "</script>":
        c.append(chunks[i+1])
for i, s in enumerate(c, 1):
    Path(f"/tmp/sas_block{i}.js").write_text(s)
    r = subprocess.run(["node", "--check", f"/tmp/sas_block{i}.js"], capture_output=True, text=True)
    st = "OK" if r.returncode == 0 else "FAIL: " + r.stderr.strip()[:120]
    print(f"block {i}: {len(s)} chars -> {st}")
print("total blocks:", len(c))
