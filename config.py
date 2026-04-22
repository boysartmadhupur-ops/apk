import os

API_KEY = os.environ.get("API_KEY", "boysart-secret-key")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
DRIVE_FOLDER_ID = os.environ.get(
    "DRIVE_FOLDER_ID", "1hYho1_aRsbdHpzJZLkhjakbrgH9-YIzE"
)
PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = os.environ.get("DATA_DIR", "data")
