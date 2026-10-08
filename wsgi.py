"""Production entry point: `gunicorn wsgi:app` (see render.yaml / Procfile).

Run ONE worker process (threads are fine). The app serialises edits with an
in-process lock (and, without DATABASE_URL, keeps SQLite on local disk), so several worker processes
would not coordinate.
"""
from smartplate import push
from smartplate.app import create_app

app = create_app()
push.start_worker()          # order-time push reminders (smartplate/push.py)
