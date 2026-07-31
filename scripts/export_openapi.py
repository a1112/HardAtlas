import json
from pathlib import Path

from hardatlas_api import app

OUTPUT = Path(__file__).resolve().parents[1] / "apps" / "api" / "openapi.json"
OUTPUT.write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"Wrote {OUTPUT}")
