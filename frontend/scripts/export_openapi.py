"""Save FastAPI's OpenAPI contract for Orval without starting a server."""

import json
from pathlib import Path

from app.main import app

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "openapi.json"
    path.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
