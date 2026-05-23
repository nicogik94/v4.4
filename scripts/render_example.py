"""Render the redacted Client Delivery Generator worked example."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAS_ROOT = REPO_ROOT / "mas"
if str(MAS_ROOT) not in sys.path:
    sys.path.insert(0, str(MAS_ROOT))

from client_delivery.service import generate_client_delivery_package  # noqa: E402


def main() -> None:
    state_path = REPO_ROOT / "examples" / "sunforest_redacted_state.json"
    output_dir = REPO_ROOT / "exports" / "example_sunforest" / "client_delivery"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    result = generate_client_delivery_package(state, output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
