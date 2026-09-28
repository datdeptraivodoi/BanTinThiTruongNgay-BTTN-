"""Compatibility entry point. Pipeline defaults to a non-sending preview."""
from bttn.pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
