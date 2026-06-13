#!/usr/bin/env python3
"""SmartPlate v1.1 entry point.

    python run.py            # start the app on http://localhost:5057
    PORT=8080 python run.py  # custom port

The database is created and seeded automatically on first run.
"""
import os

from smartplate.app import create_app
from smartplate.db import init_db
from smartplate import seed


def main() -> None:
    fresh = not os.path.exists(os.environ.get("SMARTPLATE_DB", "smartplate.db"))
    init_db()
    if fresh:
        seed.seed_all()
        print("Seeded demo catalog, users, and a sample week.")
    app = create_app()
    port = int(os.environ.get("PORT", "5057"))
    print(f"SmartPlate v1.1 running → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("DEBUG")))


if __name__ == "__main__":
    main()
