"""Gunicorn entry point for the invite beta."""
from smartplate.app import create_app

app = create_app()
