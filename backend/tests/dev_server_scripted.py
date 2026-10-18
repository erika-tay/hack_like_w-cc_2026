"""DEV ONLY: run the web app on SCRIPTED stand-in AI results, to look at the UI without a key.

    python tests/dev_server_scripted.py        (from the backend folder; serves on port 8001)

The items on screen come from tests/scripted_data.py, which I wrote by hand. They are NOT model output.
Never record the demo video or quote numbers from this mode. For the real demo, run
`python seed.py`, then `python -m ai.extract` (or `python snapshot.py load`), then `uvicorn main:app`.
"""
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
tmp = Path(tempfile.mkdtemp())
os.environ.update({"HANDOFF_DB": str(tmp / "dev.db"), "LLM_CACHE_DB": str(tmp / "c.db"), "LLM_CACHE": "0",
                   "EMBEDDER": "hash", "HANDOFF_AS_OF": os.environ.get("HANDOFF_AS_OF", "2026-10-17")})

import seed  # noqa: E402
import uvicorn  # noqa: E402
from ai import llm  # noqa: E402
from ai.extract import extract_pending  # noqa: E402
from db import get_conn  # noqa: E402
from scripted_data import RichProvider  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    seed.main()
llm.set_provider(RichProvider())
extract_pending(get_conn(), use_cache=False)
print("\n*** SCRIPTED DEV DATA, not real model output. http://127.0.0.1:8001 ***\n")

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8001)), log_level="warning")
