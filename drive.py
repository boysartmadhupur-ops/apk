"""Google Drive integration. Falls back to a sample list if no API key set."""
import requests
from config import GOOGLE_API_KEY, DRIVE_FOLDER_ID

DRIVE_LIST = "https://www.googleapis.com/drive/v3/files"
DRIVE_GET = "https://www.googleapis.com/drive/v3/files/{file_id}"

SAMPLE_TEMPLATES = [
    {"id": "sample-airpod-1", "name": "AIR PODS / AIR POD 1 W 4.132 H"},
    {"id": "sample-airpod-2", "name": "AIR PODS / AIR POD 2 WIRELESS"},
    {"id": "sample-iphone-13", "name": "APPLE / iPhone 13 Back"},
    {"id": "sample-watch-44", "name": "APPLE / 44mm Apple Watch"},
    {"id": "sample-airpod-pro", "name": "AIR PODS / AIR POD PRO"},
]


def list_templates():
    if not GOOGLE_API_KEY:
        return SAMPLE_TEMPLATES
    params = {
        "q": f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
        "key": GOOGLE_API_KEY,
        "fields": "files(id,name,mimeType)",
        "pageSize": 1000,
    }
    r = requests.get(DRIVE_LIST, params=params, timeout=30)
    r.raise_for_status()
    files = r.json().get("files", [])
    return [{"id": f["id"], "name": f["name"]} for f in files]


def download_template(file_id: str) -> bytes:
    if file_id.startswith("sample-"):
        # Minimal sample HPGL content
        return b"IN;SP1;PA0,0;PD1000,0,1000,1000,0,1000,0,0;PU;"
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not configured")
    url = DRIVE_GET.format(file_id=file_id)
    params = {"alt": "media", "key": GOOGLE_API_KEY}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.content
