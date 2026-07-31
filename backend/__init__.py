"""
backend package

Phase 1 multi-agent layer. This package sits ALONGSIDE the existing root
modules (ai_engine.py, pdf_reader.py, checklist_items.py, schemas.py,
decision_rules.py, scoring.py, pdf_report.py, app.py, api.py) and reuses
them directly rather than reimplementing their logic.

Because backend/ is a sub-package, importing e.g. `from checklist_items
import CHECKLIST_ITEMS` only works if the repo root is on sys.path. This
block guarantees that regardless of how the process was launched
(`uvicorn backend.main:app`, `python -m backend.main`, a test runner, etc.).
"""

import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_BACKEND_DIR)

# Repo root -> so `from checklist_items import ...`, `from schemas import ...`,
# `from decision_rules import ...`, `from scoring import ...`,
# `from pdf_reader import ...`, `from pdf_report import ...` all resolve to
# the existing root-level modules.
#
# NOTE: backend/ itself is deliberately NOT added to sys.path. The repo
# already has a root-level schemas.py, and backend/schemas/ is a package
# with the same import name (schemas) — adding both directories to
# sys.path would make `import schemas` ambiguous. Internal backend code
# therefore uses fully-qualified imports (`from backend.graph.state import
# ...`, `from backend.schemas.rfp_schema import ...`) rather than flat ones,
# which also sidesteps the collision entirely.
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
