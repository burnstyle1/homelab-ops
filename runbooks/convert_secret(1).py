#!/usr/bin/env python3
"""
The Secret (Byron Preiss, 1982) -> per-page Markdown for RAG ingestion.

Input : page-data spreadsheet exported from 12treasures.com (one row per page).
Output: out/english/page-XX.md and out/japanese/page-XX.md

Design note (learned the hard way): do NOT put the page identifier in a
Markdown H1 heading. Open WebUI's header-aware splitter isolates the heading
into its own chunk, producing a content-less "Page N" chunk that wins
page-number queries but carries no text. Instead, fold the page number inline
into the body prose so the identifier travels WITH the content.
"""
import openpyxl, re, os, html

SRC = "page_data_entry_3_formatted_1_.xlsx"   # adjust to your export
OUT = "./secret_corpus"

def clean(text):
    if text is None: return ""
    t = html.unescape(str(text))
    t = re.sub(r'Show more\s*\d{1,2}:\d{2}\s*[AP]M', ' ', t, flags=re.I)  # paste artifact
    t = re.sub(r'\bShow more\b', ' ', t, flags=re.I)
    t = re.sub(r'\b\d{1,2}:\d{2}\s*[AP]M\b', ' ', t, flags=re.I)
    t = re.sub(r'([A-Za-z])-\s+([a-z])', r'\1\2', t)   # stitch typeset hyphenation
    t = re.sub(r'\s+([,.;:!?])', r'\1', t)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()

def pid(slug):
    if not slug: return "unknown"
    return re.sub(r'^the-secret-page-', '', str(slug)).strip() or "unknown"

def write_doc(folder, p, title, edition_label, text, img, slug):
    if not text or len(text) < 20: return False
    os.makedirs(folder, exist_ok=True)
    tline = f'"{title}"' if title else ""
    lead  = f"The Secret, Page {p} {tline} ({edition_label} edition)."
    meta  = []
    if slug: meta.append(f"Source: https://12treasures.com/{slug}")
    if img:  meta.append(f"Scanned page image: {img}")
    body = f"{lead}\n{' '.join(meta)}\n\n{text}\n"
    with open(os.path.join(folder, f"page-{p}.md"), "w", encoding="utf-8") as f:
        f.write(body)
    return True

wb = openpyxl.load_workbook(SRC, data_only=True)
ws = wb["Page Data"]
rows = list(ws.iter_rows(min_row=2, values_only=True))
# columns: 1 post_title, 2 slug, 3 english_title, 5 english_text, 6 eng_img,
#          9 japanese_title, 12 japanese_translated, 13 jp_img
en = jp = 0
for r in rows:
    p, slug = pid(r[2]), r[2]
    title = clean(r[3]) or clean(r[1])
    if write_doc(f"{OUT}/english",  p, title, "English original", clean(r[5]), r[6], slug):  en += 1
    if write_doc(f"{OUT}/japanese", p, clean(r[9]) or title, "Japanese", clean(r[12]), r[13], slug): jp += 1
print(f"English: {en}  Japanese: {jp}")
