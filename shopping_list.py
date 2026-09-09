#!/usr/bin/env python3
"""
shopping_list.py — Shopping list generator + auto-restock for MealAgent.

Reads a meal plan (meal_plan.json, format defined in meal_planner_prompt.md),
flattens every meal's ingredients plus the other_items section, and decides
what goes on the shopping list:

  * is_staple == false  ->  fresh / non-pantry item, always on the list,
                            with its quantity from the meal plan.
  * is_staple == true   ->  ask the pantry (via pantry.py):
                              - in stock ("have")  -> do nothing
                              - depleted ("out") or not tracked at all
                                -> put it on the list (name only, no
                                   quantity) and mark it stocked in the
                                   pantry.
  * other_items         ->  never staples; always on the list.

Everything is returned in alphabetical order.

Pantry writes go exclusively through pantry.py, which owns pantry.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pantry

MEAL_PLAN_FILE = Path(__file__).parent / "meal_plan.json"


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
    skipped: list[str] = field(default_factory=list)   # staples already in stock

    @property
    def items(self) -> list[ShoppingItem]:
        """The whole list, fresh + restock, in alphabetical order."""
        return sorted(self.fresh + self.restock, key=lambda i: i.name)

    def as_dict(self) -> dict:
        return {
            "fresh": [i.as_dict() for i in self.fresh],
            "restock": [i.as_dict() for i in self.restock],
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
    if existing == incoming:
        # Two identical counts of an uncountable-looking thing: still additive.
        parsed = _parse_quantity(existing)
        if parsed:
            return _format_amount(parsed[0] * 2, parsed[1])
        return f"{existing} + {incoming}"

    left, right = _parse_quantity(existing), _parse_quantity(incoming)
    if left and right and left[1] == right[1]:
        return _format_amount(left[0] + right[0], left[1])
    return f"{existing} + {incoming}"


# ---------------------------------------------------------------------------
# Meal plan loading
# ---------------------------------------------------------------------------

def load_meal_plan(path: Path = MEAL_PLAN_FILE) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No meal plan found at {path}")
    with path.open(encoding="utf-8") as f:
        plan = json.load(f)
    if not isinstance(plan, dict):
        raise ValueError(f"{path} should contain a JSON object, got {type(plan).__name__}")
    return plan


def flatten_plan(plan: dict) -> list[dict]:
    """Every ingredient of every meal, plus other_items, as flat records.

    Each record: {name, quantity, is_staple, source}. other_items are never
    pantry staples — they're bought as individual items every week — so they
    come through with is_staple False.
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
            "is_staple": bool(item.get("is_staple", False)),
            "source": "other items",
        })

    return records


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_shopping_list(
    plan: dict,
    *,
    restock: bool = True,
) -> ShoppingList:
    """Build the shopping list and (unless restock=False) update the pantry.

    A staple lands on the list when the pantry says it's "out" or doesn't
    know about it at all. Those same names are then handed to
    `pantry.py stock`, which flips them back to "have".
    """
    pantry_state = pantry.load_pantry()
    records = flatten_plan(plan)

    fresh: dict[str, ShoppingItem] = {}
    restock_needed: dict[str, ShoppingItem] = {}
    skipped: set[str] = set()

    for record in records:
        name = record["name"]

        if not record["is_staple"]:
            item = fresh.get(name)
            if item is None:
                fresh[name] = ShoppingItem(
                    name=name,
                    quantity=record["quantity"],
                    is_staple=False,
                    sources=[record["source"]],
                )
            else:
                item.quantity = combine_quantities(item.quantity, record["quantity"])
                if record["source"] not in item.sources:
                    item.sources.append(record["source"])
            continue

        # Staple: the pantry decides.
        entry = pantry_state.get(name)
        if entry is not None and entry.get("status") == "have":
            skipped.add(name)
            continue

        item = restock_needed.get(name)
        if item is None:
            # Staples go on the list by name only — the pantry tracks
            # presence, not amounts.
            restock_needed[name] = ShoppingItem(
                name=name,
                quantity=None,
                is_staple=True,
                sources=[record["source"]],
            )
        elif record["source"] not in item.sources:
            item.sources.append(record["source"])

    shopping_list = ShoppingList(
        fresh=sorted(fresh.values(), key=lambda i: i.name),
        restock=sorted(restock_needed.values(), key=lambda i: i.name),
        skipped=sorted(skipped),
    )

    if restock and shopping_list.restock:
        restock_pantry([i.name for i in shopping_list.restock])

    return shopping_list


def restock_pantry(names: list[str]) -> None:
    """Mark staples as in stock, through pantry.py.

    `yes=True` matches `pantry.py stock ... --yes`: a staple the meal plan
    named but the pantry has never seen gets created as "uncategorized" and
    marked in stock, instead of being dropped. Neither flag path prompts, so
    this is safe to call unattended.
    """
    pantry.cmd_stock(SimpleNamespace(items=names, yes=True, no=False))


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

    total = len(shopping_list.items)
    lines.append(
        f"  {total} item(s) — {len(shopping_list.fresh)} fresh, "
        f"{len(shopping_list.restock)} restock, "
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
            "Generate this week's shopping list from meal_plan.json and "
            "restock the pantry staples that went on it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s                          # generate the list and restock the pantry
  %(prog)s --dry-run                # generate the list, leave the pantry alone
  %(prog)s --json                   # machine-readable, for agent use
  %(prog)s --flat                   # one alphabetical list, no fresh/restock split
  %(prog)s --plan other_plan.json   # read a different meal plan
""",
    )
    parser.add_argument(
        "--plan", metavar="PATH", default=str(MEAL_PLAN_FILE),
        help="Path to the meal plan JSON (default: ./meal_plan.json)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the list without writing anything to the pantry"
    )
    parser.add_argument(
        "--flat", action="store_true",
        help="Print one alphabetical list instead of splitting fresh/restock"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON to stdout (for machine/agent use)"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        plan = load_meal_plan(Path(args.plan))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not read the meal plan: {exc}", file=sys.stderr)
        sys.exit(1)

    # In --json mode the pantry.py restock chatter would pollute the JSON
    # stream, so send it to stderr for the duration of the run.
    real_stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr
    try:
        shopping_list = generate_shopping_list(plan, restock=not args.dry_run)
    finally:
        sys.stdout = real_stdout

    if args.json:
        print(json.dumps(shopping_list.as_dict(), indent=2))
    else:
        print(format_shopping_list(shopping_list, flat=args.flat))
        if args.dry_run and shopping_list.restock:
            print("  (--dry-run: pantry not updated)\n")


if __name__ == "__main__":
    main()
