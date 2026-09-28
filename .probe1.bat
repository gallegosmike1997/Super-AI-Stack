@echo off
setlocal
set "OUT=C:\Users\Michael\AppData\Local\Temp\_probe1789456491466.txt"
del "%OUT%" 2>nul
wsl.exe -e bash -c 'cd /home/kalimike/Super_AI_Stack && (
echo === PORTS ===
for p in 8000 8001 8002 8003 8004 8005 8006 8007 8008 8009 8010; do
  if curl -sS --max-time 3 http://127.0.0.1:%p/health >/dev/null 2>&1; then printf ":%p UP " ; else printf ":%p DOWN " ; fi
done
echo
echo === PAGE ===
wc -l < web/index.html
echo === SECTION IDS ===
grep -o "id=\"[^\"]*\"" web/index.html | tr -s "\n" " "
echo === SCRIPT BLOCKS ===
grep -n "<script" web/index.html
echo === TAB LINKS ===
grep -o "data-view=\"[^\"]*\"" web/index.html | tr -s "\n" " "
echo === CANVAS STRUCTURE ===
head -120 /tmp/canvas_dump/structure.txt
) > "%OUT%" 2>&1'
type "%OUT%"
