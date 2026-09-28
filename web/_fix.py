#!/usr/bin/env python3
"""Inspect index.html script region + element IDs so we can rewrite cleanly."""
from pathlib import Path
import re

h = Path("/home/kalimike/Super_AI_Stack/web/index.html")
lines = h.read_text().splitlines()

print("=== FILE LINES ===", len(lines))
print("=== SCRIPT/TAG LINES ===")
for i, ln in enumerate(lines, 1):
    if "<script" in ln or "</script>" in ln or "</body>" in ln or "</html>" in ln:
        print(f"{i:4d}| {ln[:90]}")

print("=== ALL id= IN MARKUP (before first script) ===")
first_script = next((i for i, l in enumerate(lines) if "<script" in l), len(lines))
markup = "\n".join(lines[:first_script])
ids = re.findall(r'id="([A-Za-z0-9_]+)"', markup)
for i in sorted(set(ids)):
    print("  ", i)

print("=== CHAT HANDLER + LEGACY RENDER (333-456) ===")
for i in range(332, min(456, len(lines))):
    print(f"{i+1:4d}| {lines[i]}")