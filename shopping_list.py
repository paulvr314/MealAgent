#!/usr/bin/env python3
"""
shopping_list.py — Shopping list generator + confirm step for MealAgent.

Reads a meal plan (meal_plan.json, format defined in meal_planner_prompt.md),
flattens every meal's ingredients plus the other_items section, and decides
what goes on the shopping list. The pantry, not the meal plan's is_staple
flag, is the authority on what counts as a staple:

  * name is a pantry key, status "have"  ->  skip, already on hand.
  * name is a pantry key, status "out"   ->  restock section, name only.
  * name is not a pantry key:
      - is_staple == true   ->  "untracked staples" section, with a warning.
                                Usually naming drift ("toasted sesame oil"
                                vs "sesame oil"); never auto-added.
      - otherwise           ->  fresh section, with its quantity.

Why the pantry wins: pantry_snapshot.json only lists "have" items, so the
planner can't see that an out-of-stock staple is tracked and marks it
is_staple: false. Trusting that flag would buy the staple as "fresh" and
never flip it back to "have".

Generating the list is read-only. It writes shopping_list.json; once the
order is placed, `shopping_list.py confirm` marks the restocked staples as
in stock via pantry.py. Pantry writes go exclusively through pantry.py,
which owns pantry.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pantry

BASE = Path(__file__).parent
MEAL_PLAN_FILE = BASE / "meal_plan.json"
SHOPPING_LIST_FILE = BASE / "shopping_list.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ShoppingItem:
    """One line on the shopping list."""

    name: str
    quantity: str | None = None
    is_staple: bool = False
    # Which meals / sections asked for this item — handy when the list looks
    # surprising and you want to know where a line came from.
    sources: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.name} — {self.quantity}" if self.quantity else self.name

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "is_staple": self.is_staple,
            "sources": self.sources,
        }


@dataclass
class ShoppingList:
    """The result of a generation run."""

    fresh: list[ShoppingItem] = field(default_factory=list)
    restock: list[ShoppingItem] = field(default_factory=list)
    # Marked is_staple by the planner but not a pantry key — likely drift.
    untracked: list[ShoppingItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # staples already in stock

    @property
    def items(self) -> list[ShoppingItem]:
        """The whole list in alphabetical order."""
        return sorted(self.fresh + self.restock + self.untracked, key=lambda i: i.name)

    def as_dict(self) -> dict:
        return {
            "fresh": [i.as_dict() for i in self.fresh],
            "restock": [i.as_dict() for i in self.restock],
            "untracked": [i.as_dict() for i in self.untracked],
            "skipped": self.skipped,
        }


# ---------------------------------------------------------------------------
# Quantity aggregation
# ---------------------------------------------------------------------------

_QTY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(.*?)\s*$")


def _parse_quantity(quantity: str) -> tuple[float, str] | None:
    """Split '400g' -> (400.0, 'g'), '4 fillets' -> (4.0, 'fillets'), '2' -> (2.0, '')."""
    match = _QTY_RE.match(quantity)
    if not match:
        return None
    return float(match.group(1)), match.group(2).lower()


def _format_amount(amount: float, unit: str) -> str:
    number = f"{amount:g}"
    if not unit:
        return number
    # '400g' reads better than '400 g'; '2 gallons' better than '2gallons'.
    return f"{number}{unit}" if len(unit) <= 2 else f"{number} {unit}"


def combine_quantities(existing: str | None, incoming: str | None) -> str | None:
    """Merge two quantities for the same item.

    Same unit -> add them up ('400g' + '200g' = '600g'). Anything that can't
    be added cleanly is kept side by side ('1 head + 200g') rather than
    silently dropped, so nothing goes missing from the list.
    """
    if existing is None:
        return incoming
    if incoming is None:
        return existing

    left, right = _parse_quantity(existing), _parse_quantity(incoming)
    if left and right and left[1] == right[1]:
        return _format_amount(left[0] + right[0], left[1])
    return f"{existing} + {incoming}"


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def load_meal_plan(path: Path = MEAL_PLAN_FILE) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No meal plan found at {path}")
    with path.open(encoding="utf-8") as f:
        plan = json.load(f)
    if not isinstance(plan, dict):
        raise ValueError(f"{path} should contain a JSON object, got {type(plan).__name__}")
    return plan


def save_json(path: Path, data: dict) -> None:
    """Write JSON atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def flatten_plan(plan: dict) -> list[dict]:
    """Every ingredient of every meal, plus other_items, as flat records.

    Each record: {name, quantity, is_staple, source}. other_items carry
    is_staple False; the pantry lookup still catches any that are tracked
    staples (e.g. peanut butter).
    """
    records: list[dict] = []

    for meal in plan.get("meals", []):
        meal_name = meal.get("name", "unnamed meal")
        for ingredient in meal.get("ingredients", []):
            name = pantry._normalize(ingredient.get("name", ""))
            if not name:
                continue
            records.append({
                "name": name,
                "quantity": ingredient.get("quantity"),
                "is_staple": bool(ingredient.get("is_staple", False)),
                "source": meal_name,
            })

    for item in plan.get("other_items", []):
        name = pantry._normalize(item.get("name", ""))
        if not name:
            continue
        records.append({
            "name": name,
            "quantity": item.get("quantity"),
            "is_staple": False,
            "source": "other items",
        })

    return records


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _add(bucket: dict[str, ShoppingItem], record: dict, *, staple: bool) -> None:
    """Add a record to a section, merging with an existing line of the same name."""
    name = record["name"]
    # Staples go on the list by name only — the pantry tracks presence, not amounts.
    quantity = None if staple else record["quantity"]
    item = bucket.get(name)
    if item is None:
        bucket[name] = ShoppingItem(
            name=name, quantity=quantity, is_staple=staple, sources=[record["source"]]
        )
        return
    if not staple:
        item.quantity = combine_quantities(item.quantity, quantity)
    if record["source"] not in item.sources:
        item.sources.append(record["source"])


def generate_shopping_list(plan: dict) -> ShoppingList:
    """Build the shopping list. Read-only: never touches the pantry."""
    pantry_state = pantry.load_pantry()

    fresh: dict[str, ShoppingItem] = {}
    restock: dict[str, ShoppingItem] = {}
    untracked: dict[str, ShoppingItem] = {}
    skipped: set[str] = set()

    for record in flatten_plan(plan):
        entry = pantry_state.get(record["name"])
        if entry is not None:
            if entry.get("status") == "have":
                skipped.add(record["name"])
            else:
                _add(restock, record, staple=True)
        elif record["is_staple"]:
            _add(untracked, record, staple=True)
        else:
            _add(fresh, record, staple=False)

    def by_name(bucket: dict[str, ShoppingItem]) -> list[ShoppingItem]:
        return sorted(bucket.values(), key=lambda i: i.name)

    return ShoppingList(
        fresh=by_name(fresh),
        restock=by_name(restock),
        untracked=by_name(untracked),
        skipped=sorted(skipped),
    )


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

def confirm(path: Path = SHOPPING_LIST_FILE) -> None:
    """Mark the saved list's restock staples as in stock, through pantry.py.

    Uses `stock --no` semantics: anything that isn't tracked is skipped, never
    auto-added. Running it twice is a no-op.
    """
    if not path.exists():
        print(f"No saved shopping list at {path} — run shopping_list.py first.",
              file=sys.stderr)
        sys.exit(1)
    with path.open(encoding="utf-8") as f:
        saved = json.load(f)

    if saved.get("confirmed_at"):
        print(f"This list was already confirmed at {saved['confirmed_at']}. Nothing to do.")
        return

    names = [item["name"] for item in saved.get("restock", [])]
    if names:
        print("Restocking pantry:")
        pantry.cmd_stock(SimpleNamespace(items=names, yes=False, no=True))
    else:
        print("No staples to restock.")

    untracked = [item["name"] for item in saved.get("untracked", [])]
    if untracked:
        print("\nNot tracked in the pantry (left alone). If any of these are real staples,")
        print("add them, or fix the name if it's drift from an existing key:")
        for name in untracked:
            print(f'  pantry.py add "{name}" --category <category>')

    saved["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(path, saved)
    print("\nConfirmed. If the store was out of something, run: pantry.py out \"<item>\"")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def format_shopping_list(shopping_list: ShoppingList, *, flat: bool = False) -> str:
    lines: list[str] = []

    if flat:
        lines.append("\nShopping list:\n")
        for item in shopping_list.items:
            lines.append(f"  □  {item}")
        lines.append("")
    else:
        lines.append("\nShopping list\n")
        lines.append("  FRESH THIS WEEK")
        if shopping_list.fresh:
            for item in shopping_list.fresh:
                lines.append(f"    □  {item}")
        else:
            lines.append("    (nothing)")
        lines.append("")
        lines.append("  RESTOCK")
        if shopping_list.restock:
            for item in shopping_list.restock:
                lines.append(f"    □  {item.name}")
        else:
            lines.append("    (nothing — pantry is fully stocked)")
        lines.append("")
        if shopping_list.untracked:
            lines.append("  UNTRACKED STAPLES  (not pantry keys — check for naming drift)")
            for item in shopping_list.untracked:
                lines.append(f"    □  {item.name}")
            lines.append("")

    total = len(shopping_list.items)
    lines.append(
        f"  {total} item(s) — {len(shopping_list.fresh)} fresh, "
        f"{len(shopping_list.restock)} restock, "
        f"{len(shopping_list.untracked)} untracked, "
        f"{len(shopping_list.skipped)} staple(s) already on hand\n"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shopping_list.py",
        description=(
            "Generate this week's shopping list from meal_plan.json, then "
            "`confirm` once the order is placed to restock the pantry."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s                          # generate the list (pantry untouched)
  %(prog)s --json                   # machine-readable, for agent use
  %(prog)s --flat                   # one alphabetical list, no section split
  %(prog)s --plan other_plan.json   # read a different meal plan
  %(prog)s confirm                  # order placed: mark restocked staples in stock
""",
    )
    parser.add_argument(
        "--plan", metavar="PATH", default=str(MEAL_PLAN_FILE),
        help="Path to the meal plan JSON (default: ./meal_plan.json)"
    )
    parser.add_argument(
        "--flat", action="store_true",
        help="Print one alphabetical list instead of splitting into sections"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON to stdout (for machine/agent use)"
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser(
        "confirm",
        help="Mark the saved list's restock staples as in stock (pantry.py stock --no)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "confirm":
        confirm()
        return

    try:
        plan = load_meal_plan(Path(args.plan))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not read the meal plan: {exc}", file=sys.stderr)
        sys.exit(1)

    shopping_list = generate_shopping_list(plan)

    for item in shopping_list.untracked:
        print(
            f"[warn] '{item.name}' is marked as a staple but isn't a pantry key "
            f"(from: {', '.join(item.sources)}). Possible naming drift.",
            file=sys.stderr,
        )

    save_json(SHOPPING_LIST_FILE, {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "plan": str(Path(args.plan).resolve()),
        "confirmed_at": None,
        **shopping_list.as_dict(),
    })

    if args.json:
        print(json.dumps(shopping_list.as_dict(), indent=2))
    else:
        print(format_shopping_list(shopping_list, flat=args.flat))
        if shopping_list.restock:
            print("  Once the order is placed, run: shopping_list.py confirm\n")


if __name__ == "__main__":
    main()
