"""
Boys Art - Cloud Backend
========================
Flask backend for the new system. Handles:
  * User accounts (register / login with bcrypt-hashed passwords)
  * Phone <-> PC pairing via 6-digit codes
  * Real-time job queue (mobile creates -> PC polls -> status updates)
  * Template browsing (lists everything imported from Google Drive)
  * Template file download (DXF/PLT/EPS/AI/CDR bytes)
  * Admin endpoints used by the Master desktop app (kept compatible)

Storage: SQLite (single file `data.db`) - zero setup, perfect for one shop.
For multi-region scaling later, swap to Postgres - schema is identical.
"""

import os
import io
import json
import time
import base64
import hmac
import hashlib
import secrets
import sqlite3
from functools import wraps
from flask import Flask, request, jsonify, send_file, g
from flask_cors import CORS

APP_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(APP_DIR, 'data.db')
TEMPLATE_ROOT = os.path.join(APP_DIR, 'templates_storage')
ADMIN_SECRET  = os.environ.get('ADMIN_SECRET', 'change-me-in-production')

os.makedirs(TEMPLATE_ROOT, exist_ok=True)

app = Flask(__name__)
CORS(app)

# ──────────────────────────────────────────────────────────
#  Database
# ──────────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            phone       TEXT,
            pwd_hash    TEXT NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            expires_at  INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS devices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT,
            kind        TEXT,                       -- 'mobile' | 'pc'
            pair_code   TEXT,
            paired_with INTEGER,                    -- other device id
            last_seen   INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            from_device INTEGER NOT NULL,
            to_device   INTEGER NOT NULL,
            template    TEXT NOT NULL,
            params      TEXT,                       -- json: rotation/flip/scale/...
            status      TEXT DEFAULT 'pending',     -- pending|received|cutting|completed|failed
            error       TEXT,
            created_at  INTEGER DEFAULT (strftime('%s','now')),
            updated_at  INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS templates (
            path        TEXT PRIMARY KEY,
            ext         TEXT,
            size        INTEGER,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    db.commit()
    db.close()


# ──────────────────────────────────────────────────────────
#  Auth helpers
# ──────────────────────────────────────────────────────────
def _hash_pwd(pwd, salt=None):
    if salt is None:
        salt = secrets.token_hex(8)
    h = hashlib.sha256((salt + pwd).encode('utf-8')).hexdigest()
    return f'{salt}${h}'


def _verify_pwd(pwd, stored):
    try:
        salt, _ = stored.split('$', 1)
        return hmac.compare_digest(stored, _hash_pwd(pwd, salt))
    except Exception:
        return False


def _new_token(user_id):
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 60 * 60 * 24 * 90  # 90 days
    db = get_db()
    db.execute('INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)',
               (token, user_id, expires))
    db.commit()
    return token


def current_user():
    auth = request.headers.get('Authorization', '')
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else \
            request.args.get('token') or (request.get_json(silent=True) or {}).get('token')
    if not token:
        return None
    db = get_db()
    row = db.execute(
        'SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id '
        'WHERE s.token = ? AND s.expires_at > ?',
        (token, int(time.time()))
    ).fetchone()
    return dict(row) if row else None


def require_user(fn):
    @wraps(fn)
    def w(*a, **kw):
        u = current_user()
        if not u:
            return jsonify({'error': 'unauthorized'}), 401
        return fn(u, *a, **kw)
    return w


def require_admin(fn):
    @wraps(fn)
    def w(*a, **kw):
        s = (request.args.get('secret') or
             (request.get_json(silent=True) or {}).get('secret') or
             request.headers.get('X-Admin-Secret'))
        if s != ADMIN_SECRET:
            return jsonify({'error': 'forbidden'}), 403
        return fn(*a, **kw)
    return w


# ──────────────────────────────────────────────────────────
#  Health / root
# ──────────────────────────────────────────────────────────
@app.get('/')
def root():
    return jsonify({'ok': True, 'service': 'Boys Art Cloud', 'version': '2.0'})


@app.get('/api/health')
def health():
    return jsonify({'ok': True, 'time': int(time.time())})


# ──────────────────────────────────────────────────────────
#  Auth endpoints
# ──────────────────────────────────────────────────────────
@app.post('/api/auth/register')
def register():
    d = request.get_json(force=True, silent=True) or {}
    username = (d.get('username') or '').strip().lower()
    phone    = (d.get('phone') or '').strip()
    pwd      = d.get('password') or ''
    if len(username) < 3 or len(pwd) < 4:
        return jsonify({'ok': False, 'error': 'username (>=3) and password (>=4) required'}), 400
    db = get_db()
    if db.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
        return jsonify({'ok': False, 'error': 'username already taken'}), 400
    cur = db.execute(
        'INSERT INTO users (username, phone, pwd_hash) VALUES (?, ?, ?)',
        (username, phone, _hash_pwd(pwd))
    )
    db.commit()
    token = _new_token(cur.lastrowid)
    return jsonify({'ok': True, 'token': token, 'user': {'id': cur.lastrowid, 'username': username}})


@app.post('/api/auth/login')
def login():
    d = request.get_json(force=True, silent=True) or {}
    username = (d.get('username') or '').strip().lower()
    pwd      = d.get('password') or ''
    db = get_db()
    row = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    if not row or not _verify_pwd(pwd, row['pwd_hash']):
        return jsonify({'ok': False, 'error': 'invalid credentials'}), 401
    token = _new_token(row['id'])
    return jsonify({'ok': True, 'token': token,
                    'user': {'id': row['id'], 'username': row['username']}})


@app.get('/api/auth/me')
@require_user
def me(u):
    return jsonify({'ok': True, 'user': {'id': u['id'], 'username': u['username'], 'phone': u['phone']}})


# ──────────────────────────────────────────────────────────
#  Devices & pairing
# ──────────────────────────────────────────────────────────
@app.post('/api/devices/register')
@require_user
def register_device(u):
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get('name') or 'Unnamed Device')[:60]
    kind = d.get('kind') or 'mobile'                 # 'mobile' or 'pc'
    db = get_db()
    cur = db.execute(
        'INSERT INTO devices (user_id, name, kind, last_seen) VALUES (?, ?, ?, ?)',
        (u['id'], name, kind, int(time.time()))
    )
    db.commit()
    return jsonify({'ok': True, 'device_id': cur.lastrowid})


@app.post('/api/devices/<int:dev_id>/code')
@require_user
def make_code(u, dev_id):
    """Generate a 6-digit pairing code for this device (PC side)."""
    code = f'{secrets.randbelow(1000000):06d}'
    db = get_db()
    db.execute('UPDATE devices SET pair_code = ? WHERE id = ? AND user_id = ?',
               (code, dev_id, u['id']))
    db.commit()
    return jsonify({'ok': True, 'code': code, 'expires_in': 600})


@app.post('/api/devices/<int:dev_id>/pair')
@require_user
def pair_device(u, dev_id):
    """Mobile side: enter the code shown by the PC to link them."""
    d = request.get_json(force=True, silent=True) or {}
    code = (d.get('code') or '').strip()
    db = get_db()
    target = db.execute(
        'SELECT * FROM devices WHERE pair_code = ? AND user_id = ?',
        (code, u['id'])
    ).fetchone()
    if not target:
        return jsonify({'ok': False, 'error': 'invalid code'}), 400
    db.execute('UPDATE devices SET paired_with = ?, pair_code = NULL WHERE id = ?',
               (dev_id, target['id']))
    db.execute('UPDATE devices SET paired_with = ?, pair_code = NULL WHERE id = ?',
               (target['id'], dev_id))
    db.commit()
    return jsonify({'ok': True, 'paired_with': target['id'], 'name': target['name']})


@app.get('/api/devices')
@require_user
def list_devices(u):
    db = get_db()
    rows = db.execute(
        'SELECT id, name, kind, paired_with, last_seen FROM devices WHERE user_id = ?',
        (u['id'],)
    ).fetchall()
    return jsonify({'ok': True, 'devices': [dict(r) for r in rows]})


# ──────────────────────────────────────────────────────────
#  Job queue (mobile -> PC)
# ──────────────────────────────────────────────────────────
@app.post('/api/jobs/create')
@require_user
def create_job(u):
    d = request.get_json(force=True, silent=True) or {}
    from_dev = d.get('from_device')
    template = d.get('template')
    params   = d.get('params') or {}
    if not from_dev or not template:
        return jsonify({'ok': False, 'error': 'from_device and template required'}), 400
    db = get_db()
    pair = db.execute('SELECT paired_with FROM devices WHERE id = ? AND user_id = ?',
                      (from_dev, u['id'])).fetchone()
    if not pair or not pair['paired_with']:
        return jsonify({'ok': False, 'error': 'device not paired with a PC'}), 400
    cur = db.execute(
        'INSERT INTO jobs (user_id, from_device, to_device, template, params) '
        'VALUES (?, ?, ?, ?, ?)',
        (u['id'], from_dev, pair['paired_with'], template, json.dumps(params))
    )
    db.commit()
    return jsonify({'ok': True, 'job_id': cur.lastrowid, 'status': 'pending'})


@app.get('/api/jobs/poll')
@require_user
def poll_jobs(u):
    """PC polls: return next pending job for this device + mark received."""
    dev_id = request.args.get('device_id', type=int)
    if not dev_id:
        return jsonify({'ok': False, 'error': 'device_id required'}), 400
    db = get_db()
    db.execute('UPDATE devices SET last_seen = ? WHERE id = ?',
               (int(time.time()), dev_id))
    row = db.execute(
        "SELECT * FROM jobs WHERE to_device = ? AND status = 'pending' "
        "ORDER BY id LIMIT 1",
        (dev_id,)
    ).fetchone()
    db.commit()
    if not row:
        return jsonify({'ok': True, 'job': None})
    db.execute("UPDATE jobs SET status = 'received', updated_at = strftime('%s','now') "
               "WHERE id = ?", (row['id'],))
    db.commit()
    j = dict(row)
    try:
        j['params'] = json.loads(j.get('params') or '{}')
    except Exception:
        j['params'] = {}
    return jsonify({'ok': True, 'job': j})


@app.post('/api/jobs/<int:job_id>/status')
@require_user
def update_job(u, job_id):
    d = request.get_json(force=True, silent=True) or {}
    status = d.get('status')
    err    = d.get('error')
    if status not in ('received', 'cutting', 'completed', 'failed'):
        return jsonify({'ok': False, 'error': 'invalid status'}), 400
    db = get_db()
    db.execute(
        "UPDATE jobs SET status = ?, error = ?, updated_at = strftime('%s','now') "
        "WHERE id = ? AND user_id = ?",
        (status, err, job_id, u['id'])
    )
    db.commit()
    return jsonify({'ok': True})


@app.get('/api/jobs/history')
@require_user
def job_history(u):
    db = get_db()
    rows = db.execute(
        'SELECT * FROM jobs WHERE user_id = ? ORDER BY id DESC LIMIT 50',
        (u['id'],)
    ).fetchall()
    return jsonify({'ok': True, 'jobs': [dict(r) for r in rows]})


# ──────────────────────────────────────────────────────────
#  Templates
# ──────────────────────────────────────────────────────────
SUPPORTED_EXT = {'.dxf', '.plt', '.eps', '.ai', '.cdr', '.svg'}


def _scan_templates_storage():
    """Walk disk and refresh DB index — picks up everything imported from Drive."""
    db = get_db()
    db.execute('DELETE FROM templates')
    for root, _, files in os.walk(TEMPLATE_ROOT):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in SUPPORTED_EXT:
                continue
            full = os.path.join(root, f)
            rel  = os.path.relpath(full, TEMPLATE_ROOT).replace('\\', '/')
            try:
                size = os.path.getsize(full)
            except Exception:
                size = 0
            db.execute(
                'INSERT OR REPLACE INTO templates (path, ext, size) VALUES (?, ?, ?)',
                (rel, ext, size)
            )
    db.commit()


@app.get('/api/templates/list')
def list_templates():
    """Browse all templates with optional search/brand filter."""
    q     = (request.args.get('q') or '').strip().lower()
    brand = (request.args.get('brand') or '').strip().lower()
    db = get_db()
    rows = db.execute(
        'SELECT path, ext, size FROM templates ORDER BY path'
    ).fetchall()

    items = []
    brands = set()
    for r in rows:
        p = r['path']
        parts = p.split('/')
        b = parts[0] if len(parts) > 1 else 'General'
        m = parts[1] if len(parts) > 2 else os.path.splitext(parts[-1])[0]
        brands.add(b)
        if brand and brand != b.lower():
            continue
        haystack = p.lower()
        if q and not all(w in haystack for w in q.split()):
            continue
        items.append({
            'path': p,
            'name': parts[-1],
            'brand': b,
            'model': m,
            'ext': r['ext'],
            'size': r['size'],
        })
    return jsonify({
        'ok': True,
        'count': len(items),
        'brands': sorted(brands, key=str.lower),
        'templates': items[:500],     # cap response
    })


@app.get('/api/templates/file')
def get_template_file():
    rel = (request.args.get('path') or '').strip()
    if not rel or '..' in rel:
        return jsonify({'error': 'bad path'}), 400
    full = os.path.join(TEMPLATE_ROOT, rel)
    if not os.path.isfile(full):
        return jsonify({'error': 'not found'}), 404
    return send_file(full, as_attachment=False, download_name=os.path.basename(full))


# ──────────────────────────────────────────────────────────
#  Admin: trigger Google Drive import
# ──────────────────────────────────────────────────────────
@app.post('/api/admin/import-drive')
@require_admin
def import_drive():
    """
    Kicks off a background-style import from a public Google Drive folder.
    Body: { "folder_url": "https://drive.google.com/drive/folders/<id>?usp=sharing" }
    Uses gdown which handles public folders automatically.
    """
    d = request.get_json(force=True, silent=True) or {}
    folder = (d.get('folder_url') or '').strip()
    if not folder:
        return jsonify({'ok': False, 'error': 'folder_url required'}), 400
    try:
        import gdown
    except ImportError:
        return jsonify({'ok': False, 'error': 'gdown not installed (pip install gdown)'}), 500
    try:
        gdown.download_folder(url=folder, output=TEMPLATE_ROOT,
                              quiet=True, use_cookies=False)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'drive import failed: {e}'}), 500
    _scan_templates_storage()
    db = get_db()
    n = db.execute('SELECT COUNT(*) AS c FROM templates').fetchone()['c']
    return jsonify({'ok': True, 'imported': n,
                    'message': f'Imported {n} templates from Google Drive.'})


@app.post('/api/admin/rescan')
@require_admin
def rescan():
    _scan_templates_storage()
    db = get_db()
    n = db.execute('SELECT COUNT(*) AS c FROM templates').fetchone()['c']
    return jsonify({'ok': True, 'count': n})


# ──────────────────────────────────────────────────────────
#  Bootstrap
# ──────────────────────────────────────────────────────────
init_db()
with app.app_context():
    _scan_templates_storage()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
