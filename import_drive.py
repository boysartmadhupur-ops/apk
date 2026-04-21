"""
One-shot Google Drive importer.

Run this once on the server (or your laptop) to pull every template from
your public Drive folder into ./templates_storage and refresh the index.

    python import_drive.py

Edit DRIVE_URL below if you ever change the Drive folder.
"""
import os
import sys
import sqlite3

DRIVE_URL = "https://drive.google.com/drive/folders/1hYho1_aRsbdHpzJZLkhjakbrgH9-YIzE?usp=sharing"

APP_DIR  = os.path.dirname(os.path.abspath(__file__))
DEST     = os.path.join(APP_DIR, 'templates_storage')
DB_PATH  = os.path.join(APP_DIR, 'data.db')
SUPPORTED = {'.dxf', '.plt', '.eps', '.ai', '.cdr', '.svg'}

os.makedirs(DEST, exist_ok=True)

print('[1/3] Downloading folder from Google Drive...')
print('      ', DRIVE_URL)
try:
    import gdown
except ImportError:
    print('ERROR: gdown not installed. Run:  pip install gdown')
    sys.exit(1)

try:
    gdown.download_folder(url=DRIVE_URL, output=DEST, quiet=False, use_cookies=False)
except Exception as exc:
    print(f'ERROR: download failed: {exc}')
    print('Make sure the Drive folder is shared "Anyone with the link"')
    sys.exit(1)

print('[2/3] Indexing files...')
db = sqlite3.connect(DB_PATH)
db.execute('''CREATE TABLE IF NOT EXISTS templates (
    path TEXT PRIMARY KEY, ext TEXT, size INTEGER,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
db.execute('DELETE FROM templates')
n = 0
for root, _, files in os.walk(DEST):
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext not in SUPPORTED:
            continue
        full = os.path.join(root, f)
        rel  = os.path.relpath(full, DEST).replace('\\', '/')
        db.execute('INSERT OR REPLACE INTO templates (path, ext, size) VALUES (?, ?, ?)',
                   (rel, ext, os.path.getsize(full)))
        n += 1
db.commit()
db.close()

print(f'[3/3] Done. {n} templates indexed.')
print(f'      Storage:  {DEST}')
print(f'      Database: {DB_PATH}')
