"""Generate the frontend's public API schema without reading a personal workspace."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_job_search.api import create_app
from ai_job_search.config import Config

if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "openapi.json"
    with TemporaryDirectory(prefix="ai-job-search-openapi-") as directory:
        config = Config(workspace=Path(directory) / "workspace", start_worker=False)
        schema = create_app(config).openapi()
        target.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {target.name}")
