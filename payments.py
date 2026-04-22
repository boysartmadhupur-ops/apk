"""Simple JSON-file payment store. Replace with a DB in production."""
import json
import os
import threading
from config import DATA_DIR

_LOCK = threading.Lock()
_FILE = os.path.join(DATA_DIR, "payments.json")


def _load():
    if not os.path.exists(_FILE):
        return {"approved": []}
    with open(_FILE, "r") as f:
        return json.load(f)


def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_paid(template_id: str) -> bool:
    with _LOCK:
        return template_id in _load().get("approved", [])


def approve(template_id: str):
    with _LOCK:
        data = _load()
        if template_id not in data["approved"]:
            data["approved"].append(template_id)
            _save(data)


def list_approved():
    with _LOCK:
        return _load().get("approved", [])
