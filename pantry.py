#!/usr/bin/env python3
"""
pantry.py — Pantry tracker CLI for MealAgent.

Tracks reusable pantry staples (spices, sauces, dry goods, frozen bulk items,
etc.) as have/out. Fresh ingredients consumed entirely by one recipe are never
tracked here.

Designed to be used both interactively by a human and non-interactively by the
shopping list generator / confirm workflow (use --yes / --no flags on `stock`
to suppress prompts).

pantry.json is expected to live in the same directory as this script.
"""

import argparse
import json
import sys
from pathlib import Path

PANTRY_FILE = Path(__file__).parent / "pantry.json"

KNOWN_CATEGORIES = {
    "oils", "vinegars", "sauces", "dry goods", "baking", "spices", "frozen"
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    """Canonical form: stripped, lowercased, single-spaced.
    Prevents naming drift ('Soy Sauce' vs 'soy sauce' vs 'soy  sauce').
    """
    return " ".join(name.strip().lower().split())


def load_pantry() -> dict:
    if not PANTRY_FILE.exists():
        return {}
    with PANTRY_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_pantry(pantry: dict) -> None:
    with PANTRY_FILE.open("w", encoding="utf-8") as f:
        json.dump(pantry, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args) -> None:
    """List pantry contents, optionally filtered by status and/or category."""
    pantry = load_pantry()
    items = list(pantry.items())

    if args.status:
        items = [(k, v) for k, v in items if v["status"] == args.status]

    if args.category:
        cat = _normalize(args.category)
        items = [(k, v) for k, v in items if _normalize(v.get("category", "")) == cat]

    if args.json:
        # Machine-readable output: plain dict, suitable for piping into the
        # shopping list generator. All other output goes to stderr so it does
        # not pollute the JSON stream.
        print(json.dumps(dict(items), indent=2))
        return

    if not items:
        print("No items match your filters.")
        return

    # Group by category for human-readable display
    by_category: dict[str, list] = {}
    for name, data in sorted(items):
        cat = data.get("category", "uncategorized")
        by_category.setdefault(cat, []).append((name, data))

    header_parts = []
    if args.status:
        header_parts.append(f"status={args.status}")
    if args.category:
        header_parts.append(f"category={args.category}")
    header = f"  ({', '.join(header_parts)})" if header_parts else ""
    print(f"\nPantry{header}:\n")

    for cat in sorted(by_category):
        print(f"  {cat.upper()}")
        for name, data in by_category[cat]:
            icon = "✓" if data["status"] == "have" else "✗"
            print(f"    {icon}  {name}")
        print()

    total = len(items)
    have_count = sum(1 for _, v in items if v["status"] == "have")
    out_count = total - have_count
    print(f"  {total} item(s) — {have_count} in stock, {out_count} depleted\n")


def cmd_add(args) -> None:
    """Add a new item to tracking. Exits with error if already tracked."""
    pantry = load_pantry()
    name = _normalize(args.item)

    if name in pantry:
        existing = pantry[name]
        print(
            f"'{name}' is already tracked "
            f"(status: {existing['status']}, "
            f"category: {existing.get('category', 'uncategorized')})."
        )
        sys.exit(1)

    category = _normalize(args.category) if args.category else "uncategorized"
    if args.category and category not in KNOWN_CATEGORIES:
        print(
            f"Warning: '{category}' is not a standard category. "
            f"Known categories: {', '.join(sorted(KNOWN_CATEGORIES))}."
        )

    pantry[name] = {
        "status": "have",
        "category": category,
    }
    save_pantry(pantry)
    print(f"Added '{name}'  →  category: {category}, status: have.")


def cmd_remove(args) -> None:
    """Stop tracking an item. Exits with error if not found."""
    pantry = load_pantry()
    name = _normalize(args.item)

    if name not in pantry:
        print(f"'{name}' is not tracked. Nothing to remove.")
        sys.exit(1)

    del pantry[name]
    save_pantry(pantry)
    print(f"Removed '{name}' from pantry tracking.")


def cmd_stock(args) -> None:
    """Mark one or more items as in stock.

    Items not currently in the pantry trigger an interactive prompt (or
    auto-resolve with --yes / --no for non-interactive / agent use).

    Agent usage (confirm workflow):
        pantry.py stock item1 item2 ... --no
    This silently skips any item that isn't tracked rather than prompting.
    """
    pantry = load_pantry()
    names = [_normalize(n) for n in args.items]
    updated, unknown = [], []

    for name in names:
        if name in pantry:
            pantry[name]["status"] = "have"
            updated.append(name)
        else:
            unknown.append(name)

    if updated:
        save_pantry(pantry)
        for name in updated:
            print(f"  ✓  '{name}' marked as in stock.")

    if not unknown:
        return

    # Resolve unknown items ------------------------------------------------
    if args.yes:
        # Non-interactive: add all unknown items under "uncategorized"
        for name in unknown:
            pantry[name] = {
                "status": "have",
                "category": "uncategorized",
            }
            print(f"  +  '{name}' added to pantry and marked as in stock.")
        save_pantry(pantry)

    elif args.no:
        # Non-interactive: skip all unknown items silently (agent-safe)
        for name in unknown:
            print(f"  -  '{name}' not tracked, skipping.")

    else:
        # Interactive: ask once for the whole batch
        print("\nThe following items are not currently tracked:")
        for name in unknown:
            print(f"  -  {name}")
        try:
            answer = input("\nAdd them to the pantry? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted — untracked items skipped.")
            return

        if answer == "y":
            for name in unknown:
                pantry[name] = {
                    "status": "have",
                    "category": "uncategorized",
                }
                print(f"  +  '{name}' added and marked as in stock.")
            save_pantry(pantry)
            print(
                "\nNote: newly added items have category 'uncategorized'. "
                "Use 'pantry.py remove' and re-add with --category to fix this."
            )
        else:
            print("Untracked items skipped.")


def cmd_out(args) -> None:
    """Mark one or more items as depleted. Items not in the pantry are silently ignored."""
    pantry = load_pantry()
    names = [_normalize(n) for n in args.items]
    updated, skipped = [], []

    for name in names:
        if name in pantry:
            pantry[name]["status"] = "out"
            updated.append(name)
        else:
            skipped.append(name)

    if updated:
        save_pantry(pantry)
        for name in updated:
            print(f"  ✗  '{name}' marked as depleted.")

    for name in skipped:
        print(f"  -  '{name}' not tracked, ignoring.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pantry.py",
        description="MealAgent pantry tracker — manage reusable pantry staples.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
commands:
  list    Show pantry contents (filterable by status and/or category)
  add     Add a new item to tracking (initial status: have)
  remove  Stop tracking an item entirely
  stock   Mark one or more items as in stock
  out     Mark one or more items as depleted

known categories:
  {', '.join(sorted(KNOWN_CATEGORIES))}

examples:
  %(prog)s list
  %(prog)s list --status out
  %(prog)s list --category spices
  %(prog)s list --status have --category frozen
  %(prog)s list --status out --json          # machine-readable, for agent use

  %(prog)s add "miso paste" --category sauces
  %(prog)s remove "yellow mustard"

  %(prog)s stock "olive oil" "soy sauce" "cumin"
  %(prog)s stock "olive oil" "unknown item" --yes   # auto-add unknowns
  %(prog)s stock "olive oil" "unknown item" --no    # skip unknowns silently

  %(prog)s out "olive oil" "garlic powder"

agent / confirm workflow:
  After the user confirms a grocery order, the shopping list generator calls:
    %(prog)s stock <restocked-items...> --no
  --no ensures no interactive prompt is triggered if a name slips through
  that isn't in the pantry.
""",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # -- list ----------------------------------------------------------------
    p_list = sub.add_parser("list", help="List pantry items")
    p_list.add_argument(
        "--status", choices=["have", "out"],
        help="Filter by stock status"
    )
    p_list.add_argument(
        "--category", metavar="CAT",
        help="Filter by category (e.g. spices, oils, frozen, dry goods)"
    )
    p_list.add_argument(
        "--json", action="store_true",
        help="Output raw JSON to stdout (for machine/agent use)"
    )
    p_list.set_defaults(func=cmd_list)

    # -- add -----------------------------------------------------------------
    p_add = sub.add_parser("add", help="Add a new item to tracking (status: have)")
    p_add.add_argument("item", help="Item name (quote if it contains spaces)")
    p_add.add_argument(
        "--category", metavar="CAT",
        help=f"Category to assign. Known: {', '.join(sorted(KNOWN_CATEGORIES))}"
    )
    p_add.set_defaults(func=cmd_add)

    # -- remove --------------------------------------------------------------
    p_remove = sub.add_parser("remove", help="Stop tracking an item")
    p_remove.add_argument("item", help="Item name")
    p_remove.set_defaults(func=cmd_remove)

    # -- stock ---------------------------------------------------------------
    p_stock = sub.add_parser(
        "stock",
        help="Mark items as in stock (e.g. after a restock or confirm)"
    )
    p_stock.add_argument("items", nargs="+", metavar="ITEM",
                         help="One or more item names")
    mode = p_stock.add_mutually_exclusive_group()
    mode.add_argument(
        "--yes", "-y", action="store_true",
        help="Auto-add any unknown items without prompting (agent use)"
    )
    mode.add_argument(
        "--no", "-n", action="store_true",
        help="Skip any unknown items without prompting (agent use / confirm workflow)"
    )
    p_stock.set_defaults(func=cmd_stock)

    # -- out -----------------------------------------------------------------
    p_out = sub.add_parser("out", help="Mark items as depleted")
    p_out.add_argument(
        "items", nargs="+", metavar="ITEM",
        help="One or more item names (untracked items are silently ignored)"
    )
    p_out.set_defaults(func=cmd_out)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
