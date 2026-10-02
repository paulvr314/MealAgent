# MealAgent

An adaptive weekly grocery system. It reads your preferences, pantry and past feedback,
asks Claude for a weekly meal plan, and turns that plan into a shopping list. Ordering
is manual; a `confirm` step closes the loop afterwards. Everything is local: Python
scripts and flat JSON files, with no server or database.

## Setup

Requires Python 3.11+ and an Anthropic API key.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Edit `preferences.md` to describe how you eat. It is the planner's authoritative source.

## Weekly workflow

```powershell
python meal_planner.py        # 1. generate meal_plan.json
python shopping_list.py       # 2. build the shopping list (read-only on the pantry)
# 3. order the groceries yourself
python shopping_list.py confirm   # 4. mark restocked staples as in stock
python feedback.py            # 5. record the plan and your reaction for next week
```

1. **`meal_planner.py`** snapshots the pantry, then makes one Claude call using
   `meal_planner_prompt.md`, `preferences.md` and `feedback.json`. The output is
   validated and written to `meal_plan.json`. It refuses to overwrite a plan that
   `feedback.py` hasn't recorded; use `--force` to override.
2. **`shopping_list.py`** splits the plan into *fresh* items (with quantities), staples to
   *restock*, and *untracked* staples. It prints the list and saves `shopping_list.json`.
   Options: `--flat`, `--json`, `--plan PATH`.
3. **`shopping_list.py confirm`** runs `pantry.py stock --no` on the restock items. A
   second run is a no-op.
4. **`feedback.py`** appends the week to `feedback.json`. Options: `--feedback "text"`,
   `--skip-feedback`, `--dry-run`, `--force`.

## Pantry

The pantry tracks staples (spices, oils, sauces, dry goods) as `have` or `out`, with no
quantities. Manage it only through `pantry.py`, never by editing `pantry.json`:

```powershell
python pantry.py list [--status have|out] [--category CAT] [--json]
python pantry.py add ITEM [--category CAT]
python pantry.py remove ITEM
python pantry.py stock ITEM [ITEM ...] [--yes|--no]
python pantry.py out ITEM [ITEM ...]
```

See `PANTRY_AGENT.md` for details.

## Files

| File | Purpose |
|---|---|
| `preferences.md` | How you eat (input) |
| `meal_planner_prompt.md` | Planner system prompt |
| `pantry.json` / `pantry_snapshot.json` | Pantry state / snapshot sent to the planner |
| `meal_plan.json` | This week's validated plan |
| `shopping_list.json` | Generated list (gitignored) |
| `feedback.json` | Rolling history of past weeks |

For design decisions and background, see `CLAUDE.md` and `design_plan.md`.
