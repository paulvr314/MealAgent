#!/usr/bin/env python3
"""
feedback.py — Close the weekly loop for MealAgent.

Takes the meal plan produced by meal_planner.py (meal_plan.json), appends it to
feedback.json as a new entry in "weeks", and prompts the user for their feedback
on the week.

The appended week matches the feedback.json schema:

    {
      "meals":       ["Beef and Bean Chili", ...],   # meal names only
      "other_items": ["whole milk", ...],            # other item names only
      "notes":       "...",                          # copied from the meal plan
      "feedback":    "..."                           # collected from the user
    }

Run interactively at the end of the week:

    python feedback.py

Or non-interactively (e.g. from a wrapper script):

    python feedback.py --feedback "Chili was great, salmon was dry."
"""

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
MEAL_PLAN_FILE = BASE / "meal_plan.json"
FEEDBACK_FILE = BASE / "feedback.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_meal_plan(path: Path) -> dict:
    """Read and lightly validate the meal plan."""
    if not path.exists():
        print(f"[error] {path.name} not found — run meal_planner.py first.")
        sys.exit(1)

    try:
        with path.open(encoding="utf-8") as f:
            plan = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[error] {path.name} is not valid JSON:\n{e}")
        sys.exit(1)

    if not isinstance(plan, dict) or "meals" not in plan:
        print(f"[error] {path.name} does not look like a meal plan (no 'meals' key).")
        sys.exit(1)

    return plan


def load_feedback(path: Path) -> dict:
    """Read feedback history, creating an empty structure if the file is absent."""
    if not path.exists():
        print(f"[info] {path.name} not found — starting a new history.")
        return {"weeks": []}

    try:
        with path.open(encoding="utf-8") as f:
            history = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[error] {path.name} is not valid JSON:\n{e}")
        print("Refusing to overwrite it — fix the file by hand first.")
        sys.exit(1)

    if not isinstance(history, dict):
        print(f"[error] {path.name} should contain a JSON object.")
        sys.exit(1)

    history.setdefault("weeks", [])
    if not isinstance(history["weeks"], list):
        print(f"[error] 'weeks' in {path.name} should be a list.")
        sys.exit(1)

    return history


def save_feedback(path: Path, history: dict) -> None:
    """Write feedback history back out, atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def names_from(entries, label: str) -> list[str]:
    """Pull the 'name' field out of a list of meal-plan objects."""
    names = []
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]).strip())
        elif isinstance(entry, str):
            names.append(entry.strip())
        else:
            print(f"[warn] skipping malformed {label} entry: {entry!r}")
    return names


def build_week(plan: dict) -> dict:
    """Convert a meal plan into a feedback.json week entry (feedback left blank)."""
    return {
        "meals": names_from(plan.get("meals"), "meal"),
        "other_items": names_from(plan.get("other_items"), "other item"),
        "notes": str(plan.get("notes", "")).strip(),
        "feedback": "",
    }


def already_recorded(history: dict, week: dict) -> bool:
    """True if the most recent recorded week has the same meals as this plan."""
    weeks = history.get("weeks", [])
    if not weeks:
        return False
    return weeks[-1].get("meals") == week["meals"]


def prompt_for_feedback(week: dict) -> str:
    """Show the week, then collect free-text feedback from the user."""
    print("\n" + "=" * 60)
    print("THIS WEEK'S PLAN")
    print("=" * 60)
    for name in week["meals"]:
        print(f"  • {name}")
    if week["other_items"]:
        print("\n  other items: " + ", ".join(week["other_items"]))
    print("=" * 60)

    print(
        "\nHow did the week go? What worked, what didn't, anything to change"
        "\nnext time. Blank line to finish, Ctrl-C to abort.\n"
    )

    lines: list[str] = []
    while True:
        try:
            line = input("> " if not lines else "  ")
        except EOFError:
            break
        except KeyboardInterrupt:
            print("\nAborted — nothing written.")
            sys.exit(1)

        if not line.strip():
            break
        lines.append(line.strip())

    return " ".join(lines).strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="feedback.py",
        description=(
            "Append this week's meal plan to the feedback history and record "
            "how it went."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s
  %(prog)s --feedback "Chili needed more heat; potatoes undercooked."
  %(prog)s --skip-feedback              # record the plan now, rate it later
  %(prog)s --dry-run                    # print the entry without writing
""",
    )
    parser.add_argument(
        "--meal-plan", metavar="PATH", type=Path, default=MEAL_PLAN_FILE,
        help="Meal plan to record (default: meal_plan.json)"
    )
    parser.add_argument(
        "--feedback-file", metavar="PATH", type=Path, default=FEEDBACK_FILE,
        help="Feedback history to append to (default: feedback.json)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--feedback", metavar="TEXT",
        help="Feedback text, supplied non-interactively"
    )
    mode.add_argument(
        "--skip-feedback", action="store_true",
        help="Record the week with an empty feedback field"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Append even if this plan looks like it was already recorded"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the entry that would be added without writing anything"
    )
    args = parser.parse_args()

    plan = load_meal_plan(args.meal_plan)
    history = load_feedback(args.feedback_file)
    week = build_week(plan)

    if not week["meals"]:
        print("[error] Meal plan contains no meals — nothing to record.")
        sys.exit(1)

    if already_recorded(history, week) and not args.force:
        print(
            f"[error] The last entry in {args.feedback_file.name} already has "
            "these exact meals.\n"
            "        This plan looks like it was recorded already. "
            "Use --force to append anyway."
        )
        sys.exit(1)

    if args.feedback is not None:
        week["feedback"] = args.feedback.strip()
    elif args.skip_feedback:
        week["feedback"] = ""
    else:
        week["feedback"] = prompt_for_feedback(week)
        if not week["feedback"]:
            print("[warn] No feedback given — recording the week with it blank.")

    if args.dry_run:
        print("\n[dry-run] Would append to "
              f"{args.feedback_file.name}:\n")
        print(json.dumps(week, indent=2, ensure_ascii=False))
        return

    history["weeks"].append(week)
    save_feedback(args.feedback_file, history)

    print(
        f"\nRecorded week {len(history['weeks'])} in "
        f"{args.feedback_file.name} "
        f"({len(week['meals'])} meal(s), "
        f"{len(week['other_items'])} other item(s))."
    )
    if week["feedback"]:
        print("Next week's plan will take this feedback into account.")


if __name__ == "__main__":
    main()
