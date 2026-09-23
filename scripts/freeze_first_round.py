from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.formal_plan import build_freeze, ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the offline first-round formal freeze.")
    parser.add_argument("--destination", type=Path, default=ROOT / "freezes" / "first_round_v1")
    args = parser.parse_args()
    print(json.dumps(build_freeze(args.destination, ROOT), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
