# MealAgent — Design Plan (Revision 2)

Last updated: September 2026

## What MealAgent is

An adaptive weekly meal planner that reads what pantry staples you're out of, builds a shopping list, and restocks itself the moment you confirm the order. You still place the order yourself — there is no ordering automation in the MVP.

## Scope decisions (locked in during design)

- **Pantry tracking is have/don't-have only, and only for reusable staples** — spices, sauces, oils, condiments, dry goods you buy in bulk and reuse across meals. Anything consumed entirely by one recipe (produce, meat, fish, dairy) never touches the pantry system at all. No quantities, no GUI, no third-party app (Grocy was considered and rejected as overkill).
- **The MVP ends at the shopping list, not at placing the order.** Ordering stays a manual, human step. The system's job is: meal plan → shopping list → you order manually → you run `confirm` → staples you bought are automatically flipped back to "have" in the pantry tracker. That confirm step is what closes the loop without needing browser automation.
- **No unit conversion is needed.** Because the pantry check is presence/absence rather than quantity, there's no cups-to-grams normalization problem to solve. Fresh-ingredient quantities are shown as-is from the meal plan, never subtracted against anything.

## Architecture

```
User Preferences + Feedback History
        │
        ▼
Meal Planner (LLM — Claude API)
        │  Meal Plan (JSON)
        ▼
Shopping List Generator ◄──────► Pantry Tracker (flat JSON file, text CLI)
  1. flatten + aggregate ingredients        have / out per staple item
  2. split: staple vs. fresh                add / deplete commands
  3. staple + "out"  → add to list
  4. staple + "have" → skip
  5. fresh → always add, with quantity
        │  Shopping List
        ▼
You order — manually (Instacart, Save-On-Foods, in person, etc.)
        │  you confirm the order was placed
        ▼
Auto-restock (same script) — flips every staple that was
on the list back to "have" in the pantry tracker
        │
   (optional) weekly feedback ──► feeds into next week's meal plan
```

## Components

### 1. Pantry Tracker — Easy, ~1–2 days
A single JSON file (`pantry.json`) mapping item name → `{"status": "have"|"out", "last_updated": ...}`, plus a small CLI: `pantry.py add "soy sauce"`, `pantry.py out "soy sauce"`, `pantry.py list`. No server, no GUI. Build it yourself — realistically under 100 lines.

The one thing that has to be decided up front, in plain text, before any code: **the canonical list of ~20–40 tracked staples.** Everything downstream treats that list as the source of truth for what counts as "pantry business" at all.

Watch for: naming drift ("soy sauce" vs. "soya sauce" creating duplicate entries — normalize casing/whitespace and use one canonical name per item).

### 2. Meal Planner — Easy–Medium, ~2–4 days
One Claude API call per week. `system` prompt establishes the role; `user` message contains a preferences file (dietary rules, liked cuisines, disliked ingredients, portion sizes, nights cooked vs. eaten out, macro targets) plus recent feedback (last 4–8 weeks of ratings/notes). Ask for structured JSON output, validate with Pydantic.

Watch for: variety (LLMs repeat themselves — explicitly ask for variety and pass in recently-used meals to avoid repeats); keep ingredient naming plain and consistent so staple-matching in the next step works reliably.

### 3. Shopping List Generator + Auto-restock — Easy–Medium, ~3–4 days (MVP finish line)
Pure Python, no LLM call. Flatten and aggregate the week's ingredients. For each one, check it against the staples list: match → check pantry status, skip if "have," include (no quantity, just the name) if "out." No match → it's fresh, always include with its quantity from the meal plan. Output grouped as "fresh this week" / "restock." A `confirm` command flips every staple that was on the list back to "have."

Watch for: matching ingredient names to the staples list reliably; partial-fulfillment drift if a store is out of something you meant to restock (low-stakes, fixed with a manual `pantry.py out "..."`); deciding what "confirm" means (order placed vs. groceries arrived) and staying consistent.

### 4. Grocery Order Automation — Hard, explicitly NOT in the MVP
Deferred stretch goal. If ever revisited: no major Canadian grocer exposes a public cart API, so the path would be Playwright browser automation against something like Instacart — search each item, add best match to cart, stop before checkout, pay manually. Real ongoing maintenance burden (sites change layout). Only worth doing once the core loop has run for a few weeks and proven the lists themselves are good.

## Tech stack

- **Python 3.11+** — the whole system as a handful of scripts sharing JSON files, no web framework needed
- **anthropic** SDK — meal planner LLM calls
- **Pydantic** — validates `MealPlan`, `Ingredient`, `ShoppingList` models
- **Flat JSON files** — `pantry.json`, `feedback.json`; no SQLite, no Grocy, no server
- **argparse** (stdlib) — pantry CLI

Explicitly cut from an earlier draft: Grocy (solves problems — quantities, expiry, barcodes — that don't exist once tracking is have/don't-have only), `pint`/unit conversion (no quantity math left to do), Playwright (not needed while ordering stays manual).

## Build roadmap

- **Phase 0 (~1–2 days):** Pantry tracker — write the staples list, build the CLI, seed real current state.
- **Phase 1 (~2–4 days):** Meal planner — wire up the Anthropic API, iterate on the prompt until output is structured and plausible.
- **Phase 2 (~3–4 days) — MVP:** Shopping list generator + auto-restock. Complete end-to-end: preferences → meal plan → shopping list → confirm → pantry updated.
- **Phase 3 (optional, ~2–3 days):** Feedback loop — weekly rating prompt, feeds into next planning run. Worth adding once you've used the MVP for a couple of weeks.
- **Phase 4 (someday, ~5–10 days):** Automated ordering via Playwright. Only after the loop above is proven.

**Total MVP estimate: 1–2 weeks**, working part-time.

## Immediate next steps

1. Write the staples list — the ~20–40 items the pantry will actually track.
2. Build the pantry CLI (`pantry.py add/out/list`) and seed it with your real current pantry state.
3. Write `preferences.json` and run a first, unstructured meal-plan prompt against the Claude API to see what the model produces before locking in data models.