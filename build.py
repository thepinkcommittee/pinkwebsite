#!/usr/bin/env python3
import os, re, html
from pathlib import Path

ROOT = Path(__file__).parent
ENTRIES_DIR = ROOT / 'entries'
ASSETS_DIR = ROOT / 'assets'
OUTPUT_DIR = ROOT / 'hacks'
BROWSE_DIR = ROOT / 'browse'
INDEX_FILE = ROOT / 'index.html'

FRONT_RE = re.compile(r"^(.*)\n---\n(.*)$", re.S)
IMG_TOKEN = re.compile(r"!([\w\-\.@]+)")


def parse_hack(text):
	m = FRONT_RE.match(text)
	if not m:
		raise ValueError('missing --- separator')
	head, body = m.groups()
	meta = {}
	for line in head.splitlines():
		line = line.strip()
		if not line or line.startswith('#'):
			continue
		if ':' not in line:
			continue
		k, v = line.split(':', 1)
		meta[k.strip().lower()] = v.strip()
	meta['body'] = body.strip()
	# collect inline assets
	assets = IMG_TOKEN.findall(meta['body'])
	meta['assets_inline'] = assets
	# pick preview = first image if present
	meta['preview'] = assets[0] if assets else ''
	return meta


def body_with_images_rendered(body):
	# Replace === with dotted dividers
	body = re.sub(r'^===\s*$', '<hr class="rule">', body, flags=re.MULTILINE)
	
	# Replace !filename with figure+img tags
	def repl(m):
		fname = m.group(1)
		src = f"../assets/{fname}"
		return f"<figure>\n\t<img src=\"{src}\" data-dither=\"gray4\" alt=\"\" loading=\"lazy\">\n</figure>"
	# escape non-image text paragraphs; keep double-newline as paragraph breaks
	parts = [p for p in re.split(r"(\![\w\-\.@]+)", body)]
	out = []
	for part in parts:
		if not part:
			continue
		if part.startswith('!'):
			out.append(repl(re.match(r"\!([\w\-\.@]+)", part)))
		else:
			paras = [f"<p>{html.escape(x.strip())}</p>" if x.strip() and not x.strip().startswith('<hr') else x.strip() for x in part.split('\n\n') if x.strip()]
			out.extend(paras)
	return "\n".join(out)


def render_entry_page(meta):
	entry_id = meta.get('id')
	title = html.escape(meta.get('title', entry_id))
	date = html.escape(meta.get('date', ''))
	loc = html.escape(meta.get('location', ''))
	status = html.escape(meta.get('status', ''))
	perp = html.escape(meta.get('perpetrators', ''))
	contrib = html.escape(meta.get('contributors', ''))
	topic = html.escape(meta.get('topic', ''))
	body_html = body_with_images_rendered(meta.get('body', ''))
	return f"""<!doctype html>
<html lang=\"en\">
<head>
<!-- Google tag (gtag.js) -->
<script async src=\"https://www.googletagmanager.com/gtag/js?id=G-YT6PVB4WVN\"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());

  gtag('config', 'G-YT6PVB4WVN');
</script>
<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>{title}</title>
<link rel=\"stylesheet\" href=\"../styles.css\"> 
<script src=\"../dither.js\" defer></script>
</head>
<body>
<div class=\"backbar\"><a href=\"../index.html\">back</a></div><header class=\"head\"><h1>{title}</h1></header>
<main class=\"container\">
\t<div class=\"meta\">
\t\t<div>date: {date}</div>
\t\t<div>location: {loc}</div>
\t\t<div>status: {status}</div>
\t\t<div>perpetrators: {perp}</div>
\t\t<div>contributors: {contrib}</div>
\t\t<div>topic: {topic}</div>
\t</div>
\t<hr class=\"rule\">
\t{body_html}
</main>
<footer class=\"foot\"><p><a href=\"../index.html\">back</a></p></footer>
</body>
</html>"""


def render_index_item(meta, is_browse=False):
	entry_id = meta['id']
	title = html.escape(meta.get('title', entry_id))
	date = html.escape(meta.get('date', ''))
	loc = html.escape(meta.get('location', ''))
	preview = meta.get('preview', '').strip()
	
	# Fix paths for browse pages vs index
	if is_browse:
		entry_link = f"../hacks/{entry_id}.html"
		preview_src = f"../assets/{preview}" if preview else ''
	else:
		entry_link = f"hacks/{entry_id}.html"
		preview_src = f"assets/{preview}" if preview else ''
		
	toggle_html = f'<span class="toggle" onclick="togglePreview(this)">▼</span>' if preview_src else ''
	preview_html = f'<img class="preview grayscale" data-dither="gray4" src="{preview_src}" alt="{title}" loading="lazy">' if preview_src else ''
	
	return f'''\t<li class="hack-item">
\t\t{toggle_html}
\t\t<div>
\t\t\t<a class="hack" href="{entry_link}">
\t\t\t\t<span class="when">{date}</span>
\t\t\t\t<span class="title">{title}</span>
\t\t\t\t<span class="where">({html.escape(loc)})</span>
\t\t\t</a>
\t\t\t{preview_html}
\t\t</div>
\t</li>'''


def write_browse_pages(entries):
	BROWSE_DIR.mkdir(parents=True, exist_ok=True)
	# helpers to extract keys
	by_year = {}
	by_perp = {}
	by_loc = {}
	by_topic = {}
	for m in entries:
		year = (m.get('date','') or '')[:4]
		by_year.setdefault(year, []).append(m)
		perp = m.get('perpetrators','').strip() or 'unknown'
		by_perp.setdefault(perp, []).append(m)
		loc = m.get('location','').strip() or 'unknown'
		by_loc.setdefault(loc, []).append(m)
		for t in [x.strip() for x in m.get('topic','').split(',') if x.strip()]:
			by_topic.setdefault(t, []).append(m)

	def page(title, groups, sort_reverse=False):
		items = []
		if sort_reverse:
			# Sort years numerically in descending order
			sorted_keys = sorted(groups.keys(), key=lambda x: int(x) if x.isdigit() else 0, reverse=True)
		else:
			# Sort alphabetically
			sorted_keys = sorted(groups.keys())
		for key in sorted_keys:
			items.append(f"<h3>{html.escape(key)}</h3>\n<ul class=\"hacklist\">\n" + "\n".join(render_index_item(m, is_browse=True) for m in groups[key]) + "\n</ul>")
		toggle_script = """<script>
function togglePreview(toggle) {
	var preview = toggle.nextElementSibling.querySelector('.preview');
	if (preview) {
		preview.classList.toggle('show');
		toggle.textContent = preview.classList.contains('show') ? '▲' : '▼';
	}
}
</script>"""
		return f"<!doctype html><html lang=\"en\"><head><!-- Google tag (gtag.js) --><script async src=\"https://www.googletagmanager.com/gtag/js?id=G-YT6PVB4WVN\"></script><script>window.dataLayer = window.dataLayer || [];function gtag(){{dataLayer.push(arguments);}}gtag('js', new Date());gtag('config', 'G-YT6PVB4WVN');</script><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title><link rel=\"stylesheet\" href=\"../styles.css\"><script src=\"../dither.js\" defer></script></head><body><div class=\"backbar\"><a href=\"../index.html\">back</a></div><header class=\"head\"><h1>{html.escape(title)}</h1></header><main class=\"container\">{''.join(items)}</main><footer class=\"foot\"><p><a href=\"../index.html\">back</a></p></footer>{toggle_script}</body></html>"

	(BROWSE_DIR / 'by_year.html').write_text(page('Browse by year', by_year, sort_reverse=True), encoding='utf-8')
	(BROWSE_DIR / 'by_perpetrator.html').write_text(page('Browse by perpetrator', by_perp), encoding='utf-8')
	(BROWSE_DIR / 'by_location.html').write_text(page('Browse by location', by_loc), encoding='utf-8')
	(BROWSE_DIR / 'by_topic.html').write_text(page('Browse by topic', by_topic), encoding='utf-8')


def build():
	OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
	entries = []
	for f in sorted(ENTRIES_DIR.glob('*.hack')):
		meta = parse_hack(f.read_text(encoding='utf-8'))
		if 'id' not in meta:
			meta['id'] = f.stem
		entries.append(meta)
		(OUTPUT_DIR / f"{meta['id']}.html").write_text(render_entry_page(meta), encoding='utf-8')

	# Sort entries by date (year) in descending order for recent section
	entries_by_date = sorted(entries, key=lambda x: x.get('date', ''), reverse=True)

	# inject recent list between markers in index.html
	idx = INDEX_FILE.read_text(encoding='utf-8')
	start = '<!-- BUILD:RECENT:START -->'
	end = '<!-- BUILD:RECENT:END -->'
	if start not in idx or end not in idx:
		idx = idx.replace('<ul class="hacklist">', '<ul class="hacklist">\n' + start)
		idx = idx.replace('</ul>', end + '\n\t</ul>', 1)
	items = "\n".join(render_index_item(m) for m in entries_by_date)
	import re as _re
	idx = _re.sub(_re.escape(start) + r"[\s\S]*?" + _re.escape(end), start + "\n" + items + "\n\t" + end, idx)
	INDEX_FILE.write_text(idx, encoding='utf-8')

	write_browse_pages(entries)
	print(f"built {len(entries)} entries and browse pages")


if __name__ == '__main__':
	build() 