from pathlib import Path
import subprocess, sys

h = Path("/home/kalimike/Super_AI_Stack/web/index.html").read_text(encoding="utf-8")

print("===VIEW SECTIONS===")
for m in __import__("re").finditer(r'id="view-\w+"', h):
    print(m.group(), end="  ")
print()

print("\n===CANVAS IMAGES===")
imgs = sorted(set(__import__("re").findall(r"SAS%20[A-Za-z]+\.png", h)))
for i in imgs:
    print(i)

print("\n===NEW PANEL IDS===")
ids = ("healthscore","uptime","reqmin","latavg","p50","rail","reslist","t-req","t-stream",
       "t-err","t-tools","succ","stackpct","cogrow","pipeline","routing","railbackend","hubstats",
       "healthtbl","backend","residentmeter","residentnum","residentfill","mstats","layerstatus",
       "streams","memtext","memadd","memout","memq","memsearch","memhits","sess2","sysbars",
       "p95","qdepth","corelist","alertfeed","events","log","concepts","hexwrap","hex","sat","pyr")
present = [i for i in ids if f'id="{i}"' in h]
missing = [i for i in ids if f'id="{i}"' not in h]
print("present:", len(present), "/", len(ids))
if missing:
    print("MISSING:", ", ".join(missing))
print("\n===SCRIPT BLOCKS===")
for i, line in enumerate(h.splitlines(), 1):
    if "<script" in line or "</script>" in line:
        print(i, line.strip()[:90])

print("\n===NODE CHECK===")
for blk in h.split("</script>"):
    if "<script>" in blk or "<script " in blk:
        js = blk.split("<script", 1)[1].strip()
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=5)
        print("node exit", r.returncode, "stderr:", r.stderr.strip()[:120])
