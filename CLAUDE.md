# MealAgent — Project Context

> Context document for Claude Code. Consolidated 2026-09-30 from the design notes,
> prompt, preferences, and channel research produced during the planning phase.

---

## 1. What this is

MealAgent is an adaptive weekly grocery system. It combines the user's preferences,
habits and lifestyle with its own reasoning to produce a weekly meal plan and the
grocery order that backs it, then learns week over week from feedback on previous
orders.

The long-term vision is that it places the order itself. **The MVP deliberately stops
at the shopping list** — ordering is a manual human step, and a `confirm` command
closes the loop afterwards.

Single user, single machine, local files. No server, no database, no web framework.

---

## 2. Current state

| Component | Status |
|---|---|
| `pantry.py` — pantry tracker CLI | **Built.** Complete, documented in `PANTRY_AGENT.md`. Atomic writes. |
| `meal_planner.py` + `meal_planner_prompt.md` | **Built.** Forced tool use → Pydantic-validated `meal_plan.json`. Several real runs. Refuses to overwrite an unrecorded plan (`--force` to override); warns on invariant violations. |
| `pantry_snapshot.py` | **Built.** Called by `meal_planner.py` before each run. |
| `shopping_list.py` + `confirm` | **Built — MVP complete.** Generation is read-only and writes `shopping_list.json`; `shopping_list.py confirm` restocks via `pantry.py stock --no`. |
| `feedback.py` | **Built.** Appends the current plan + user feedback as a week in `feedback.json`. |
| Order automation | **Not built, deliberately deferred.** Research done — see §9. |

Known open issue: the weekly calorie arithmetic in `preferences.md` doesn't add up
(see §11).

---

## 3. Architecture

```
preferences.md  +  feedback.json  +  pantry_snapshot.json
        │
        ▼
meal_planner.py  (one Claude API call, Pydantic-validated)
        │  meal_plan.json
        ▼
shopping_list.py  ◄──── reads ────  pantry.json   (read-only at this step)
  1. flatten + aggregate ingredients and other_items
  2. pantry key + "out"   → restock section (name only, no quantity)
  3. pantry key + "have"  → skip
  4. not a pantry key, is_staple true → "untracked" section + drift warning
  5. everything else      → fresh section, with quantity
        │  shopping list (printed + shopping_list.json)
        ▼
User orders manually (Instacart / Save-On-Foods / in person)
        │  user runs `shopping_list.py confirm`
        ▼
pantry.py stock <restocked items...> --no
        │
   end of week: feedback.py → appended to feedback.json → next week's plan
```

---

## 4. Locked design decisions

These were settled during design. Don't relitigate them without a reason.

**Pantry is presence/absence only, staples only.** `have` or `out`, no quantities, no
expiry, no barcodes. It tracks reusable staples — spices, oils, vinegars, sauces,
condiments, dry goods, baking supplies, frozen bulk. Anything consumed entirely by one
recipe (produce, meat, fish, dairy) never enters the pantry system; it goes straight on
the shopping list with its quantity.

**Because of that, there is no unit-conversion problem.** No cups-to-grams
normalisation, no `pint`, no quantity arithmetic anywhere. Fresh quantities pass through
verbatim from the meal plan.

**The MVP ends at the shopping list.** Ordering stays manual. `confirm` is what closes
the loop without browser automation.

**Explicitly rejected:** Grocy (solves quantity/expiry/barcode problems that don't exist
here), SQLite (flat JSON is enough), Playwright (not needed while ordering is manual),
any web framework.

**The pantry, not the planner, decides what's a staple.** The snapshot only lists
`have` items, so the planner can't see that an out-of-stock staple is tracked and may
mark it `is_staple: false`. `shopping_list.py` therefore classifies by pantry key
membership; the LLM's flag only matters for names the pantry doesn't know.

**Name normalisation is the backbone.** Every pantry key is stripped, lowercased, and
internally single-spaced. The LLM is instructed to use the exact snapshot spelling.
Naming drift (`sesame oil` vs `toasted sesame oil`) is the single most likely source of
silent bugs in this system — treat it as such.

---

## 5. Tech stack

- Python 3.11+ (uses `str | None` unions)
- `anthropic` SDK — planner calls, currently `claude-sonnet-4-6`, `max_tokens=8192`
- `pydantic` — validates `MealPlan` / `Meal` / `Ingredient` / `OtherItem`
- `python-dotenv` — `ANTHROPIC_API_KEY`
- `argparse` (stdlib) — pantry CLI
- Flat JSON files for all state

---

## 6. Data contracts

### `pantry.json` — managed only through `pantry.py`, never edited directly

```json
{
  "olive oil":     { "status": "have", "category": "oils" },
  "garlic powder": { "status": "out",  "category": "spices" }
}
```

`status` ∈ `{"have", "out"}`. Categories: `baking`, `dry goods`, `frozen`, `oils`,
`sauces`, `spices`, `vinegars`; anything else becomes `uncategorized`.

### `pantry.py` CLI

```
pantry.py list  [--status have|out] [--category CAT] [--json]
pantry.py add    ITEM [--category CAT]      # new item, status: have; exit 1 if exists
pantry.py remove ITEM                       # exit 1 if not found; human-facing only
pantry.py stock  ITEM [ITEM ...] [--yes|--no]
pantry.py out    ITEM [ITEM ...]            # untracked names silently ignored
```

Exit codes: `0` success, `1` not found / already exists.

**Automation rules.** Always pass `--json` when reading. Always pass `--no` to `stock`
— without it, unknown items trigger an interactive prompt that will hang a script.
`out` is idempotent and prompt-free, safe to call anywhere.

### `pantry_snapshot.json` — written fresh before each planning run

Grouped by category, names only (the planner doesn't need status; it receives what's
in stock):

```json
{
  "spices": ["cumin", "garam masala", "garlic powder"],
  "oils":   ["olive oil", "sesame oil"]
}
```

### `meal_plan.json` — the planner's validated output

```json
{
  "meals": [
    {
      "name": "Chicken Tikka Masala",
      "estimated_calories": 650,
      "servings": 2,
      "ingredients": [
        { "name": "chicken thighs", "quantity": "500g", "is_staple": false },
        { "name": "cumin",          "quantity": null,   "is_staple": true  }
      ]
    }
  ],
  "other_items": [
    { "name": "whole milk", "quantity": "2 gallons" },
    { "name": "cucumbers",  "quantity": null }
  ],
  "notes": "Plain-language paragraph explaining the week's choices."
}
```

Invariants the planner is asked to keep (it warns on violations; `shopping_list.py`
doesn't rely on the staple flag — see §4):
- `is_staple: true` ⟹ `quantity` is `null`, and the name matches a pantry key if one exists.
- `is_staple: false` ⟹ `quantity` is a string with units (`"400g"`, `"2 fillets"`).
- `other_items` are non-meal items — snacks, beverages. Components eaten together but
  not cooked together are separate entries (`yoghurt` and `frozen mango`, not one line).
- `estimated_calories` is per serving.

Note: the Pydantic `OtherItem` model has no `is_staple` field, while the prompt's
example output includes one. Harmless today (Pydantic ignores extras by default) but
worth reconciling.

### `feedback.json` — rolling history

```json
{
  "weeks": [
    {
      "meals": ["Beef and Bean Chili", "Sheet Pan Salmon and Vegetables"],
      "other_items": ["whole milk", "cashews"],
      "notes": "Planner's own reasoning from that week.",
      "feedback": "User's free-text reaction."
    }
  ]
}
```

Appended by `feedback.py` (interactive, or `--feedback "..."` / `--skip-feedback`).

### `shopping_list.json` — written by each `shopping_list.py` run (gitignored)

`{generated_at, plan, confirmed_at, fresh, restock, untracked, skipped}`. `confirm`
reads `restock`, stocks those names with `--no` semantics, and sets `confirmed_at`, so a
second `confirm` is a no-op. Regenerating the list resets it.

---

## 7. The planner prompt

`meal_planner_prompt.md` is the system prompt, loaded verbatim from disk at runtime. It
is a substantial piece of the product; treat edits to it as product changes, not config
tweaks.

What it instructs the model to do:

1. Read `preferences.md` as the authoritative source on how the user eats.
2. Read `pantry_snapshot.json` — aim to buy **1–2 new spices/sauces per week**, avoid
   phantom restocks where a near-equivalent meal would use what's on hand, but never
   silently substitute away from a dish's identity (fish sauce in a Thai dish stays).
3. Read `feedback.json` — no repeats within 2 weeks; weeks 3–6 only if highly rated;
   free again after 8. Negative notes are standing amendments until contradicted.
4. Generate: no cuisine more than twice, no protein in more than two meals, cooking
   methods must genuinely vary, at least one meal unseen in 8 weeks.

Output comes back through forced tool use (`submit_meal_plan`, schema derived from the
Pydantic models), so there's no text or fences to strip. `meal_planner.py` revalidates
with Pydantic and exits non-zero with the raw tool input printed on failure.

Named failure modes the prompt guards against: wrong output format (the most critical),
naming drift, phantom restocks, feedback blindness, variety theater (different proteins
in the same base sauce is not variety), wrong meal count, preference overreach
(inventing coverage for things the preferences don't mention), and calorie fiction.

---

## 8. User profile (drives everything)

Source of truth is `preferences.md`. Summary for orientation:

22M, 1.94 m, 85–90 kg, training ~5 days/week. **3,100–3,300 kcal/day, ≥110 g protein**,
with attention to iron and a reasonable amount of fruit and veg across the week.

**Plan per week:** 2 meal-prep meals (each batch covers ~4 servings, eaten as lunches
and dinners) + 2 quick meals + snacks. **Do not plan breakfast** — handled ad hoc. **Do
not plan eaten-out meals** — roughly 2 lunches and 2 dinners a week, outside the system.

**Cooking style.** Minimal prep work is a mild but real preference: baby carrots over
chopping, jarred minced garlic over cloves, bagged spinach and pre-cut florets, canned
or frozen where it works. Few dishes — one-pot and sheet-pan meals are ideal.
Occasional exceptions are fine; these aren't rigid rules.

**Known-good meal preps:** chili, slow-cooked pork, chicken curry, tacos/burritos,
sausages with greens, pasta with tomato sauce, sheet-pan salmon and vegetables.
**Quick meals:** frozen dumplings with a vegetable; oatmeal and eggs.
**Snacks:** cashews, BBQ rice crackers, corn thins, good sourdough with hummus, yoghurt
with frozen fruit, frozen mango, bananas with peanut butter, trail mix (not
peanut/raisin-heavy), smoothies, crunchy cucumbers or pickles.
**Beverages:** 2 gallons whole milk/week.
**Bulk, outside the weekly order:** oats, rice.

---

## 9. Ordering channel research (for Phase 4)

Researched 2026-09-15 for Vancouver west side / UBC (V6T).

**The core finding: checkout is not the hard part — the catalog is.** Turning
"500 g extra-firm tofu" into a specific in-stock SKU at a known price at a specific
store is what breaks grocery agents. A viable channel must offer a queryable catalog, a
cart object an agent can populate without owning payment, and a human-confirmable review
step. That review step is a feature, not a weakness: what the user swaps, drops or
increases each week is exactly the training signal the learning loop needs. This is also
why email/text ordering was deprioritised — simple to send, nearly worthless to learn
from.

- **Instacart Developer Platform** — verified, and weaker than its reputation. The
  public API is essentially one endpoint: send ingredient *names*, receive *a URL*. No
  catalog, pricing, user or order data; cannot pin a store; items are not auto-added to
  a cart. Canada supported, ~30–40 day approval. Terms ban scraping and alternative
  checkout. A legitimate low-effort **v1 fallback** (~3–5 min of human tapping/week),
  not a route to zero-touch.
- **Save-On-Foods** — best major-chain candidate. Own delivery fleet, fully permissive
  `robots.txt`, category sitemap with stable numeric IDs, and also carried on Instacart
  so the fallback path covers the same store.
- **Stong's (Dunbar)** — best strategic candidate. `shop.stongs.com` runs on the
  Homesome headless platform; the sitemap exposes saved lists, past purchases, *plural*
  carts, and a checkout-confirm step. $50 minimum, $10.99 delivery to Dunbar, free
  pickup. The real argument: it's a business you can email. Homesome markets headless
  APIs — a UBC student with a research project is a plausible route to sanctioned
  access, which no amount of reverse-engineering will ever get from Loblaw or Sobeys.
- **Ruled out / unresolved:** Legends Haul (now wholesale, out); Choices (outsources to
  Instacart, no added surface); Voilà (best reorder primitives anywhere, but BC coverage
  unverified — check it, and if it serves V6T it jumps the queue); SPUD.ca (model is the
  closest to what the user described, but every fetch 403'd — worth 5 minutes in a
  browser); PC Express (robots-disallowed, thin west-side coverage).

**Recommended shape when this is built:** separate order *generation* from order
*placement* behind a channel adapter.

```
MealAgent → structured order (name, qty, unit, constraints) → ChannelAdapter → store
```

Do the catalog-mapping work once, behind the adapter, and cache the
ingredient→product-ID mapping. That cache is reusable across every channel and is where
the actual engineering value sits.

Open checks (~20 min in a browser): inspect each storefront's add-to-cart network call
for endpoint and auth scheme; `curl -sSI` each for bot-detection headers (`cf-ray`,
`_abck`/`bm_sz`, `_px*`); test V6T 1Z4 in each delivery checker (UBC is University
Endowment Lands, a genuine coverage edge case); confirm Voilà BC coverage; email
Stong's/Homesome.

---

## 10. Roadmap

- **Phase 0 — done.** Pantry tracker: staples list, CLI, seeded state.
- **Phase 1 — done.** Meal planner: Anthropic API wired, prompt iterated, output validated.
- **Phase 2 — next, ~3–4 days. This completes the MVP.** Shopping list generator +
  `confirm`. Pure Python, no LLM call. Flatten and aggregate the week's ingredients,
  split staple vs. fresh per §3, output grouped as "fresh this week" / "restock", and
  implement `confirm` to flip every restocked staple back to `have`.
- **Phase 3 — ~2–3 days.** Close the feedback loop: a weekly rating prompt that appends
  a week entry to `feedback.json`. Worth doing after a couple of weeks of real use.
- **Phase 4 — someday, ~5–10 days.** Automated ordering, starting with the Instacart
  link handoff as v1 and a real adapter as v2. Only after the loop above has proven the
  lists themselves are good.

---

## 11. Things to watch

- **Naming drift** between meal-plan ingredient names and pantry keys. This is the
  system's main failure mode. Normalise on both sides; consider logging any
  `is_staple: true` ingredient whose name doesn't match a pantry key, rather than
  silently treating it as fresh.
- **`confirm` semantics.** Decide once whether it means "order placed" or "groceries
  arrived" and stay consistent. Partial fulfillment (store was out of something) is
  low-stakes — fix with a manual `pantry.py out "..."`.
- **Interactive prompts in automation.** `pantry.py stock` without `--yes`/`--no` will
  block on stdin. Any scripted call must pass a flag.
- **LLM repetition.** Models repeat themselves; variety has to be actively enforced via
  feedback history, and the `notes` field must honestly account for the rotation rather
  than claim variety it didn't deliver.
- **Calorie accounting.** The prompt demands the plan match the stated daily target
  without over- or undershooting, which is a hard constraint given eaten-out meals sit
  outside the plan. Check whether the planner's arithmetic in `notes` actually holds up.
  **Known inconsistency (2026-10-01):** `preferences.md` says the plan should cover
  17,000–17,500 kcal/week, but the plan covers ~10 meals (8 meal-prep servings + 2 quick
  meals). At the stated 1,000–1,300 kcal per meal that's ~11–12k. The last plan totalled
  ~9,850. The 17k figure or the per-meal targets need reconciling — a user decision.
