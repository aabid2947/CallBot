"""FastAPI app wiring transport + voice pipeline + core, for THIS project.

App-level glue, not part of the publishable core. Run with `python -m server`.
"""

from .app import app, create_app

__all__ = ["app", "create_app"]

