# pantry.py — Agent Reference

This document is the authoritative reference for AI agents interacting with the
MealAgent pantry tracker. Read it in full before issuing any pantry commands.

---

## What the pantry tracks

Reusable staples only: spices, oils, vinegars, sauces, dry goods, baking
supplies, and frozen bulk items. These are things bought in bulk and used across
many recipes.

**Fresh ingredients are never tracked here.** Produce, meat, fish, and dairy are
consumed entirely by individual recipes and belong on the shopping list directly
— not in the pantry system.

---

## Files

| File | Purpose |
|------|---------|
| `pantry.py` | CLI — the only way to read or write pantry state |
| `pantry.json` | State store — do not edit directly |

Both files live in the same directory. `pantry.json` is managed exclusively
through `pantry.py`.

---

## Data schema

`pantry.json` is a flat JSON object keyed by canonical item name:

```json
{
  "olive oil":    { "status": "have", "category": "oils" },
  "garlic powder": { "status": "out",  "category": "spices" }
}
```

`status` is always one of `"have"` or `"out"`. There are no quantities.

### Known categories

`baking`, `dry goods`, `frozen`, `oils`, `sauces`, `spices`, `vinegars`

Items added without a category receive `"uncategorized"`.

---

## Name normalisation

All item names are normalised before any read or write:
- stripped of leading/trailing whitespace
- lowercased
- internal whitespace collapsed to a single space

`"Soy Sauce"`, `"soy sauce"`, and `"  soy  sauce  "` are all the same key.
Always pass names in their canonical lowercase form to avoid ambiguity.

---

## Commands

### `list` — read pantry state

```
python pantry.py list [--status have|out] [--category CAT] [--json]
```

| Flag | Effect |
|------|--------|
| `--status have` | Return only in-stock items |
| `--status out` | Return only depleted items |
| `--category CAT` | Filter to one category (e.g. `spices`, `dry goods`) |
| `--json` | Emit JSON to stdout; nothing else is written to stdout |

**Agent use:** always pass `--json` so output is machine-readable. Combine with
`--status` and `--category` to narrow results.

```bash
# All depleted staples (primary input to the shopping list generator)
python pantry.py list --status out --json

# Full pantry state
python pantry.py list --json

# All tracked spices, regardless of status
python pantry.py list --category spices --json
```

JSON output shape:
```json
{
  "olive oil":    { "status": "out", "category": "oils" },
  "garlic powder": { "status": "out", "category": "spices" }
}
```

---

### `stock` — mark items as in stock

```
python pantry.py stock ITEM [ITEM ...] [--no | --yes]
```

Sets the `status` of each named item to `"have"`. Items must already be tracked
unless `--yes` is passed.

| Flag | Effect on untracked items |
|------|--------------------------|
| *(none)* | Interactive prompt — **do not use in automation** |
| `--no` / `-n` | Skip silently — safe for all agent use |
| `--yes` / `-y` | Add to pantry as `"uncategorized"` and mark in stock |

**Agent use:** always pass `--no` unless you have a specific reason to add new
items. `--no` will never block on input.

```bash
# Confirm workflow — restock everything that was on the shopping list
python pantry.py stock "olive oil" "garlic powder" "cumin" --no
```

---

### `out` — mark items as depleted

```
python pantry.py out ITEM [ITEM ...]
```

Sets the `status` of each named item to `"out"`. Items not currently tracked
are **silently ignored** — this command is always safe to call from automation
without a `--no` flag.

```bash
python pantry.py out "olive oil" "garlic powder"
```

---

### `add` — add a new item to tracking

```
python pantry.py add ITEM [--category CAT]
```

Adds a single new item with `status: have`. Exits with code `1` if the item is
already tracked. Prefer assigning a known category; items without one receive
`"uncategorized"`.

```bash
python pantry.py add "miso paste" --category sauces
```

---

### `remove` — stop tracking an item

```
python pantry.py remove ITEM
```

Deletes the item from `pantry.json` entirely. Exits with code `1` if not found.
This is a human-facing maintenance command; agents should not call it during
normal workflows.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Item not found, or item already exists (see `add`) |

---

## Standard agent workflows

### Shopping list generation

```bash
# 1. Get all depleted staples → these go on the restock section of the list
python pantry.py list --status out --json

# 2. Get full pantry → use to check whether a meal-plan ingredient is tracked
python pantry.py list --json
```

For each ingredient in the meal plan:
- If the ingredient name matches a key in the pantry JSON and its status is
  `"out"` → add to the restock section (name only, no quantity).
- If it matches and status is `"have"` → skip.
- If it does not match any key → it's a fresh ingredient; add to the fresh
  section with its quantity from the meal plan.

### Confirm workflow (after user places the grocery order)

```bash
# Flip every staple that was on the restock list back to "have"
python pantry.py stock "olive oil" "garlic powder" "cumin" ... --no
```

Pass `--no` so untracked names (e.g. a fresh ingredient that slipped through)
are skipped silently rather than causing a prompt.

### Marking an item depleted mid-week

```bash
python pantry.py out "sesame oil"
```

The `out` command is idempotent and ignores untracked names — safe to call any
time.
