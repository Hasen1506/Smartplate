#!/usr/bin/env python3
"""SmartPlate v1.1 entry point.

    python run.py            # start the app on http://localhost:5057
    PORT=8080 python run.py  # custom port

Set SMARTPLATE_MODE=demo for sample data; public mode requires configured services.
"""
import os

from smartplate.app import create_app


def main() -> None:
    app = create_app()
    port = int(os.environ.get("PORT", "5057"))
    print(f"SmartPlate running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG") == "1")


if __name__ == "__main__":
    main()
