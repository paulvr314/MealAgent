#!/usr/bin/env python3
"""
pantry_snapshot.py — Generate pantry_snapshot.json for MealAgent.

Reads the current pantry state via pantry.py and writes a fresh
pantry_snapshot.json containing only in-stock ("have") items, grouped by
category. Out-of-stock items are omitted entirely — their absence is what
signals the meal planner to treat them as unavailable.

meal_planner.py calls main() automatically before each run; it can also be
run on its own:
    python pantry_snapshot.py

Output file: pantry_snapshot.json (same directory as this script).
"""

import json
from pathlib import Path

from pantry import load_pantry

SNAPSHOT_FILE = Path(__file__).parent / "pantry_snapshot.json"


def generate_snapshot() -> dict[str, list[str]]:
    """
    Build the snapshot dict: category → sorted list of in-stock item names.

    Only "have" items appear. "out" items are excluded — their absence is the
    signal the meal planner uses to treat them as unavailable.
    """
    pantry = load_pantry()

    snapshot: dict[str, list[str]] = {}
    for name, data in pantry.items():
        if data.get("status") != "have":
            continue
        category = data.get("category", "uncategorized")
        snapshot.setdefault(category, []).append(name)

    # Sort each category's list for stable, readable output
    for items in snapshot.values():
        items.sort()

    # Sort categories alphabetically too
    return dict(sorted(snapshot.items()))


def main() -> None:
    snapshot = generate_snapshot()

    with SNAPSHOT_FILE.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Summary to stdout
    total = sum(len(v) for v in snapshot.values())
    print(f"pantry_snapshot.json written — {total} in-stock item(s) across "
          f"{len(snapshot)} category/categories.")
    if total == 0:
        print("Warning: snapshot is empty. All pantry items may be marked 'out', "
              "or pantry.json may not exist yet.")


if __name__ == "__main__":
    main()
