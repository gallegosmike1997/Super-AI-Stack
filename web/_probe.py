#!/usr/bin/env python3
import json, subprocess, urllib.request
from pathlib import Path

root = Path("/home/kalimike/Super_AI_Stack")
out = []

def sh(cmd, timeout=12):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except Exception as e:
        return f"ERR: {e}"

out.append("===GIT===")
out.append(sh("cd /home/kalimike/Super_AI_Stack && git --no-pager status --short 2>&1 | head -25"))
out.append("===LOG===")
out.append(sh("cd /home/kalimike/Super_AI_Stack && git --no-pager log --oneline -5 2>&1"))

out.append("===PORTS===")
for p in [8000,8001,8002,8003,8005,8006,8007,8008,8010]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/health", timeout=3) as r:
            out.append(f":{p} -> {r.read().decode()[:130]}")
    except Exception as e:
        out.append(f":{p} -> DOWN ({e})")

out.append("===DIAG===")
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/diagnostics", timeout=12) as r:
        d = json.loads(r.read())
        out.append(json.dumps(d, indent=2)[:1800])
except Exception as e:
    out.append(f"DIAG ERR: {e}")

out.append("===PAGE===")
index = root / "web/index.html"
out.append(f"index.html lines: {len(index.read_text().splitlines())}")
import re
view_ids = re.findall(r'id="view-[a-z]+"', index.read_text())
out.append("view-section ids: " + ", ".join(v.replace('id="','').replace('view-','').rstrip('"') for v in view_ids))
txt = index.read_text()
out.append(f"polling placeholders: {txt.count('polling')}")
out.append("canvas-key ids present: " + ", ".join(sorted(set(
    i for i in
    __import__("re").findall(r'id="(healthscore|reslist|cogrow|sysbars|memchips|archtiers|brain|conceptlist|modtable|rail|events|alerts|latencychart|statcards|pipeline)"', txt)
))))

out.append("===PATCHJS===")
p = root / "web/_patch.js"
out.append(f"_patch.js exists: {p.exists()}, lines: {len(p.read_text().splitlines()) if p.exists() else 0}")

out.append("===CANVAS===")
c = Path("/tmp/canvas_dump")
out.append(f"canvas_dump exists: {c.exists()}")
if c.exists():
    out.append("files: " + ", ".join(x.name for x in c.iterdir() if x.is_file())[:400])
    s = c / "structure.txt"
    if s.exists():
        out.append("===CANVAS_HEAD===\n" + s.read_text()[:1500])

out.append("===MODEL_MANAGER===")
mm = root / "model_manager/main.py"
out.append(f"model_manager exists: {mm.exists()}")
out.append(sh("cd /home/kalimike/Super_AI_Stack && git --no-pager diff --cached --stat 2>&1 | tail -8"))

out.append("===COMPILE===")
out.append(sh("cd /home/kalimike/Super_AI_Stack && .venv/bin/python -m py_compile gateway/main.py router/main.py common/model_client.py common/utils.py vision/main.py llm_general/main.py llm_coding/main.py llm_reasoning.py 2>&1; .venv/bin/python -m py_compile llm_reasoning/main.py 2>&1", 10))

Path("/tmp/sas_state.txt").write_text("\n".join(out))
print("wrote /tmp/sas_state.txt")
