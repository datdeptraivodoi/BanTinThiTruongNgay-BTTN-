"""Offline fixture preview using the production pipeline; never sends email."""
from pathlib import Path

from bttn.pipeline import main

if __name__ == "__main__":
    root = Path(__file__).parent
    raise SystemExit(main([
        "--create-only", "--snapshot", str(root / "tests/fixtures/snapshot.json"),
        "--content", str(root / "tests/fixtures/content.json"),
    ]))
