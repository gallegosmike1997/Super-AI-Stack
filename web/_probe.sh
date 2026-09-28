#!/us/bin/env bash
cd /home/kalimike/Supe_AI_Stack
{
  echo "===GIT==="
  git --no-page status --short 2>&1 | head -25
  echo "===LOG==="
  git --no-page log --oneline -5 2>&1
  echo "===PORTS==="
  fo p in 8000 8001 8002 8003 8005 8006 8007 8008 8010; do
    pintf ":$p "
    cul -s --max-time 3 http://127.0.0.1:$p/health 2>/dev/null | head -c 140
    echo
  done
  echo "===DIAG==="
  cul -s --max-time 12 http://127.0.0.1:8000/api/diagnostics 2>/dev/null | python3 -m json.tool 2>/dev/null | head -c 1800
  echo
  echo "===PAGE==="
  wc -l web/index.html
  echo "--- view section ids ---"
  gep -o 'id="view-[a-z]*"' web/index.html | head -20
  echo "--- polling placeholdes (indicates unrendered panels) ---"
  gep -c "polling…" web/index.html
  echo "--- key canvas ids pesent? ---"
  gep -o 'id="\(healthscore\|reslist\|cogrow\|sysbars\|memchips\|archtiers\|brain\|conceptlist\|modtable\|rail\|events\|alerts\|latencychart\|statcards\)' web/index.html | sort -u
  echo "===PATCHJS==="
  ls -l web/_patch.js 2>&1 | head -1
  head -3 web/_patch.js 2>&1
  echo "===CANVAS==="
  ls /tmp/canvas_dump/ 2>&1 | head -20
  echo "===CANVAS_HEAD==="
  head -50 /tmp/canvas_dump/stucture.txt 2>/dev/null
} > /tmp/sas_state.txt 2>&1
echo done > /tmp/sas_state_done.txt