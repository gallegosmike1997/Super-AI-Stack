from pathlib import Path

w = Path(__file__).parent
html = (w / "index.html").read_text(encoding="utf-8")
sec1 = (w / "_sec1.html").read_text(encoding="utf-8")
sec2 = (w / "_sec2.html").read_text(encoding="utf-8")
css = (w / "_css.txt").read_text(encoding="utf-8")
patch = (w / "_patch.js").read_text(encoding="utf-8")

# 1. Replace the six view sections (dashboard start .. </div><script> boundary).
start = html.index('<section class="grid cols" id="view-dashboard">')
end = html.index("</div><script>", start)
html = html[:start] + sec1 + "\n" + sec2 + "\n" + html[end:]

# 2. Append the new panel CSS just before </style>.
k = html.index("</style>")
html = html[:k] + css + "</style>" + html[k + len("</style>"):]

# 3. Append the render-patch script before </body> so it overrides earlier fns.
html = html.replace("</body>", "<script>\n" + patch + "\n</script>\n</body>")

(w / "index.html").write_text(html, encoding="utf-8")
print("assembled:", len(html.splitlines()), "lines,", len(html), "bytes")
for probe in ["view-concept", "cogrow", "healthscore", "hexwrap", "_sec1", "_patch.js"]:
    assert probe in html if not probe.startswith("_") else probe not in html, probe
print("probes OK")
