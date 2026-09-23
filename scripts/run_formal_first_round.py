"""CLI entrypoint; no network without --execute."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.formal_runner import main

if __name__ == "__main__":
    main()
