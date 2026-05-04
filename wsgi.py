"""
Production entry point.
Serves ceo_pilot.html at / and the Flask API at /api/*.
Run locally:   python wsgi.py
Run with gunicorn (production):  gunicorn wsgi:app
"""
import os
import shutil
import sys
from pathlib import Path

# Allow `import processor` and `import app` from the backend folder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# Seed the data directory on first deploy.
# If DATA_DIR is a mounted volume (/var/data on Render) and empty,
# copy the bundled CSV seed files so the app starts with sample data.
_env_data = os.getenv("DATA_DIR")
if _env_data:
    src = Path(__file__).parent / "backend" / "data"
    dst = Path(_env_data)
    dst.mkdir(parents=True, exist_ok=True)
    for csv_file in src.glob("*.csv"):
        target = dst / csv_file.name
        if not target.exists():
            shutil.copy2(csv_file, target)

from app import app  # noqa: E402 — must come after sys.path update
from flask import send_from_directory

ROOT = os.path.dirname(__file__)


@app.route("/")
def index():
    return send_from_directory(ROOT, "ceo_pilot.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
