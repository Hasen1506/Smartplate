"""Production entry point: `gunicorn wsgi:app` (see render.yaml / Procfile).

Run ONE worker process (threads are fine). The app serialises edits with an
in-process lock and keeps SQLite on local disk, so several worker processes
would not coordinate.
"""
from smartplate.app import create_app

app = create_app()
