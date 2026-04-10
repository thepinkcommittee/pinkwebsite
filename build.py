#!/usr/bin/env python3
import os, re, html, random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
ENTRIES_DIR = ROOT / 'entries'
ASSETS_DIR = ROOT / 'assets'
OUTPUT_DIR = ROOT / 'hacks'
BROWSE_DIR = ROOT / 'browse'
INDEX_FILE = ROOT / 'index.html'

FRONT_RE = re.compile(r"^(.*)\n---\n(.*)$", re.S)
IMG_TOKEN = re.compile(r"!([^\n!]+?\.(?:png|jpg|jpeg|gif|webp|svg))", re.I)
NON_ID_CHAR_RE = re.compile(r"[^a-z0-9]+")
NEWS_TIMESTAMP_ENV = 'PINK_NEWS_TIMESTAMP'
NEW_ENTRY_STEMS_ENV = 'PINK_NEW_ENTRY_STEMS'
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$")
FALLBACK_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+06:09:\d{2}$")
NEWS_SECTION_LIST_RE = re.compile(r'(<section id="news">[\s\S]*?<ul class="hacklist">)([\s\S]*?)(</ul>)', re.S)
NEWS_ITEM_RE = re.compile(
	r'<li class="hack-item"(?P<attrs>[^>]*)>\s*<div(?:\s+[^>]*)?>\s*<span class="when">(?P<when>.*?)</span>\s*<span class="title">(?P<title>.*?)</span>\s*<span class="where">\((?P<where>.*?)\)</span>\s*</div>\s*</li>',
	re.S,
)


def parse_csv_env(value):
	return {item.strip() for item in value.split(',') if item.strip()}


def fallback_news_time():
	return f"06:09:{random.randint(0, 59):02d}"


def normalize_news_timestamp(raw_timestamp, fallback_date=''):
	timestamp = (raw_timestamp or '').strip()
	fallback_date = (fallback_date or '').strip()
	if DATETIME_RE.match(timestamp):
		return " ".join(timestamp.split())
	if DATE_RE.match(timestamp):
		return f"{timestamp} {fallback_news_time()}"
	if TIME_RE.match(timestamp) and DATE_RE.match(fallback_date):
		return f"{fallback_date} {timestamp}"
	if not timestamp and DATE_RE.match(fallback_date):
		return f"{fallback_date} {fallback_news_time()}"
	return timestamp


def adjust_pr_creation_timestamp(raw_timestamp):
	timestamp = normalize_news_timestamp(raw_timestamp)
	if not DATETIME_RE.match(timestamp):
		return timestamp
	if FALLBACK_DATETIME_RE.match(timestamp):
		return timestamp
	try:
		dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
		return (dt - timedelta(hours=4)).strftime('%Y-%m-%d %H:%M:%S')
	except ValueError:
		return timestamp


def news_sort_key(timestamp):
	for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
		try:
			return datetime.strptime(timestamp, fmt)
		except ValueError:
			continue
	return datetime.min


def news_key_for(timestamp, title):
	normalized_title = (title or '').strip().lower()
	return f"{normalized_title}|{timestamp}"


def generate_backend_entry_id(source_stem, used_ids):
	base = NON_ID_CHAR_RE.sub('-', (source_stem or '').strip().lower()).strip('-') or 'entry'
	candidate = base
	suffix = 2
	while candidate in used_ids:
		candidate = f"{base}-{suffix}"
		suffix += 1
	used_ids.add(candidate)
	return candidate


def news_item_from_meta(meta, raw_timestamp):
	title = (meta.get('title', '') or meta.get('_source_stem', '') or '').strip().lower()
	location = (meta.get('location', '') or '').strip()
	entry_date = (meta.get('date', '') or '').strip()
	timestamp = timestamp_for_added_item(entry_date, raw_timestamp)
	return {
		'title': title,
		'location': location,
		'timestamp': timestamp,
		'is_added': True,
	}


def timestamp_for_added_item(entry_date, preferred_timestamp):
	entry_date = (entry_date or '').strip()
	preferred_timestamp = (preferred_timestamp or '').strip()

	if not DATE_RE.match(entry_date):
		return normalize_news_timestamp(preferred_timestamp, entry_date)

	normalized_preferred = normalize_news_timestamp(preferred_timestamp, entry_date)
	if DATETIME_RE.match(normalized_preferred):
		time_part = normalized_preferred.split()[1]
		return f"{entry_date} {time_part}"

	if TIME_RE.match(normalized_preferred):
		return f"{entry_date} {normalized_preferred}"

	return normalize_news_timestamp(entry_date, entry_date)


def parse_existing_news_items(news_content, entries_by_title):
	items = []
	for match in NEWS_ITEM_RE.finditer(news_content):
		when_text = html.unescape(match.group('when')).strip()
		title_text = html.unescape(match.group('title')).strip()
		location_text = html.unescape(match.group('where')).strip()
		added_match = re.match(r'^\s*added:\s*(.*)$', title_text, flags=re.I)
		is_added = bool(added_match)
		title = (added_match.group(1) if added_match else title_text).strip().lower()
		location = location_text

		meta = entries_by_title.get(title)
		if meta and 'location' in meta:
			location = meta['location']
		entry_date = (meta.get('date', '') if meta else '') or ''
		if is_added and DATE_RE.match(entry_date.strip()):
			timestamp = timestamp_for_added_item(entry_date.strip(), when_text)
		else:
			timestamp = normalize_news_timestamp(when_text, entry_date)

		items.append(
			{
				'title': title,
				'location': location,
				'timestamp': timestamp,
				'is_added': is_added,
			}
		)
	return items


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
		key = k.strip().lower()
		if key == 'id':
			continue
		meta[key] = v.strip()
	meta['body'] = body.strip()
	# collect inline assets
	assets = [asset.strip() for asset in IMG_TOKEN.findall(meta['body'])]
	meta['assets_inline'] = assets
	# pick preview = first image if present
	meta['preview'] = assets[0] if assets else ''
	return meta


def body_with_images_rendered(body):
	# Replace === with dotted dividers
	body = re.sub(r'^===\s*$', '<hr class="rule">', body, flags=re.MULTILINE)
	
	# Replace !filename with figure+img tags
	def repl(fname):
		fname = fname.strip()
		src = f"../assets/{fname}"
		return f"<figure>\n\t<img src=\"{src}\" data-dither=\"gray4\" alt=\"\" loading=\"lazy\">\n</figure>"

	def append_paragraphs(text, out):
		paras = [
			f"<p>{html.escape(x.strip())}</p>" if x.strip() and not x.strip().startswith('<hr') else x.strip()
			for x in text.split('\n\n')
			if x.strip()
		]
		out.extend(paras)

	# escape non-image text paragraphs; keep double-newline as paragraph breaks
	out = []
	last_end = 0
	for match in IMG_TOKEN.finditer(body):
		append_paragraphs(body[last_end:match.start()], out)
		out.append(repl(match.group(1)))
		last_end = match.end()
	append_paragraphs(body[last_end:], out)
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
		<div>location: {loc}</div>
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
	raw_title = (meta.get('title', entry_id) or entry_id).strip()
	title = html.escape(raw_title)
	date = html.escape(meta.get('date', ''))
	raw_loc = (meta.get('location', '') or '').strip()
	loc = html.escape(raw_loc)
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
			<a class="hack" href="{entry_link}">
\t\t\t\t<span class="when">{date}</span>
\t\t\t\t<span class="title">{title}</span>
				<span class="where">({loc})</span>
\t\t\t</a>
\t\t\t{preview_html}
\t\t</div>
\t</li>'''


def render_news_item(item):
	timestamp = item.get('timestamp', '')
	raw_title = (item.get('title', '') or '').strip()
	title = html.escape(raw_title)
	title_prefix = 'added: ' if item.get('is_added', True) else ''
	date = html.escape(timestamp)
	raw_loc = (item.get('location', '') or '').strip()
	loc = html.escape(raw_loc)
	return f'''\t\t\t\t<li class="hack-item">
					<div>
						<span class="when">{date}</span>
						<span class="title">{title_prefix}{title}</span>
						<span class="where">({loc})</span>
					</div>
				</li>'''


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
	used_ids = set()
	for f in sorted(ENTRIES_DIR.glob('*.hack')):
		meta = parse_hack(f.read_text(encoding='utf-8'))
		meta['id'] = generate_backend_entry_id(f.stem, used_ids)
		meta['_source_stem'] = f.stem
		entries.append(meta)
		(OUTPUT_DIR / f"{meta['id']}.html").write_text(render_entry_page(meta), encoding='utf-8')

	# Sort entries by date (year) in descending order for recent section
	entries_by_date = sorted(entries, key=lambda x: x.get('date', ''), reverse=True)

	# Inject generated news and recent lists between markers in index.html.
	# Rebuild the entire news list in sorted date-time order, with managed markers.
	idx = INDEX_FILE.read_text(encoding='utf-8')
	news_start = '<!-- BUILD:NEWS:START -->'
	news_end = '<!-- BUILD:NEWS:END -->'
	news_indent = '\t\t\t\t'
	new_entry_stems = parse_csv_env(os.getenv(NEW_ENTRY_STEMS_ENV, ''))
	news_timestamp = adjust_pr_creation_timestamp(os.getenv(NEWS_TIMESTAMP_ENV, '').strip())
	entries_by_title = {(m.get('title', '') or '').strip().lower(): m for m in entries if (m.get('title', '') or '').strip()}
	news_section_match = NEWS_SECTION_LIST_RE.search(idx)
	existing_news_content = ''
	if news_section_match:
		existing_news_content = news_section_match.group(2).replace(news_start, '').replace(news_end, '').strip('\r\n')
	existing_news_items = parse_existing_news_items(existing_news_content, entries_by_title)
	existing_news_keys = {
		news_key_for(item.get('timestamp', ''), item.get('title', '')) for item in existing_news_items
	}

	new_news_items = []
	for meta in entries_by_date:
		if meta.get('_source_stem') not in new_entry_stems:
			continue
		news_item = news_item_from_meta(meta, news_timestamp or meta.get('date', ''))
		news_key = news_key_for(news_item.get('timestamp', ''), news_item.get('title', ''))
		if news_key in existing_news_keys:
			continue
		existing_news_keys.add(news_key)
		new_news_items.append(news_item)

	all_news_items = existing_news_items + new_news_items
	all_news_items.sort(key=lambda item: news_sort_key(item.get('timestamp', '')), reverse=True)
	news_items = "\n".join(render_news_item(item) for item in all_news_items)
	news_block = "\n" + news_indent + news_start + "\n" + news_items + "\n" + news_indent + news_end + "\n" + news_indent
	if news_section_match:
		idx = NEWS_SECTION_LIST_RE.sub(lambda m: m.group(1) + news_block + m.group(3), idx, count=1)

	start = '<!-- BUILD:RECENT:START -->'
	end = '<!-- BUILD:RECENT:END -->'
	import re as _re
	if start not in idx or end not in idx:
		idx = idx.replace('<ul class="hacklist">', '<ul class="hacklist">\n' + start)
		idx = idx.replace('</ul>', end + '\n\t</ul>', 1)
	items = "\n".join(render_index_item(m) for m in entries_by_date)
	idx = _re.sub(_re.escape(start) + r"[\s\S]*?" + _re.escape(end), start + "\n" + items + "\n\t" + end, idx)
	INDEX_FILE.write_text(idx, encoding='utf-8')

	write_browse_pages(entries)
	print(f"built {len(entries)} entries and browse pages")


if __name__ == '__main__':
	build() 