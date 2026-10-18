import re, glob, os, json

# ---------- 1. gather per-file basic data ----------
def parse_frontmatter(path):
    with open(path, encoding='utf-8') as f:
        content = f.read()
    m = re.match(r'^---\n(.*?)\n---\n(.*)$', content, re.S)
    if not m:
        return {}, content
    fm_text, body = m.groups()
    fm = {}
    for line in fm_text.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()
    return fm, body

files = {}
for d in ['facts','procedures','rules']:
    for path in sorted(glob.glob(f'memory/{d}/*.md')):
        fid = os.path.basename(path)[:-3]
        size = os.path.getsize(path)
        fm, body = parse_frontmatter(path)
        files[path] = dict(dir=d, id=fid, size=size, fm=fm, body=body)

# ---------- 2. dates from git log ----------
dates = {}
with open('derived/scratch/2026-09-02-inventar/dates.tsv', encoding='utf-8') as f:
    for line in f:
        parts = line.rstrip('\n').split('\t')
        if len(parts) == 3:
            dates[parts[0]] = (parts[1], parts[2])

# untracked files fallback
untracked_mtime = {
    'memory/rules/chinit-srazu-chuzhimi-rukami.md': '2026-09-01',
    'memory/rules/oborvannoe-zadanie-itog-ne-v-result.md': '2026-09-01',
    'memory/rules/ozhidanie-otveta-trebuet-oprosa.md': '2026-08-31',
    'memory/rules/zapret-proveryaetsya-cepochkoy.md': '2026-08-31',
}
for p, dt in untracked_mtime.items():
    if p not in dates:
        dates[p] = (dt, dt + ' (не закоммичено)')

# ---------- 3. entities ----------
ENTITIES = [
    'Николай', 'Кочубеев', 'No Logo', 'NDA', 'Диадок', 'Госключ', 'Контур.Сайн',
    'HH.ru', 'FL.ru', 'FarPost', 'Avito', 'Telegram', 'MAX', 'ВК', 'VK',
    'Notion', 'Сбер', 'Alverra', 'TrendFlow', 'Figma', 'Gmail', 'Piper',
    'Илья', 'Whisper', 'mail.ru', 'Госуслуги', 'Anthropic', 'Claude',
]
def find_entities(text):
    found = []
    for e in ENTITIES:
        if e in text:
            found.append(e)
    return found

for path, rec in files.items():
    rec['entities'] = find_entities(rec['body'])
    d = dates.get(path, ('?','?'))
    rec['created_git'] = d[0]
    rec['updated_git'] = d[1]

with open('derived/scratch/2026-09-02-inventar/files.json', 'w', encoding='utf-8') as f:
    json.dump(files, f, ensure_ascii=False, indent=1)

print("done", len(files))
