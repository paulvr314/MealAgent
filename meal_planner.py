#!/usr/bin/env python3
"""
MealAgent — Meal Planner
Run: python meal_planner.py

Output is obtained via forced tool use: the MealPlan schema is passed to the
API as a tool and `tool_choice` requires the model to call it. The plan comes
back as a parsed dict in the tool_use block's `input`, so there is no prose
preamble to strip, no markdown fences to handle, and no JSON parsing step.
"""

import argparse
import json
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

import feedback
import pantry
import pantry_snapshot

load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
TOOL_NAME = "submit_meal_plan"
# 2 meal-prep + 2 quick meals, per preferences.md. Used only for a warning.
EXPECTED_MEALS = 4

# ── Pydantic models ───────────────────────────────────────────────────────────
# Field descriptions are surfaced to the model through the tool schema, so keep
# them accurate — they are load-bearing prompt text, not just documentation.

class Ingredient(BaseModel):
    name: str = Field(
        description=(
            "Simplest common name for the ingredient. If it corresponds to a "
            "pantry staple, use the exact name as it appears in "
            "pantry_snapshot.json."
        )
    )
    quantity: str | None = Field(
        description=(
            "Always null when is_staple is true. Always a string with units "
            'when is_staple is false (e.g. "400g", "2 fillets", "1 head").'
        )
    )
    is_staple: bool = Field(
        description=(
            "True for reusable pantry-type items (spices, oils, vinegars, "
            "sauces, condiments, dry goods, baking supplies, frozen bulk), "
            "whether or not they appear in the pantry snapshot. False for "
            "fresh ingredients consumed by the recipe (produce, meat, fish, "
            "dairy)."
        )
    )

class Meal(BaseModel):
    name: str = Field(description="Dish name.")
    estimated_calories: int | None = Field(
        default=None,
        description="Realistic calories per serving, for the stated serving size.",
    )
    servings: int = Field(description="Number of servings this meal produces.")
    ingredients: list[Ingredient]

class OtherItem(BaseModel):
    name: str = Field(description="Snack, beverage, or other non-meal item.")
    quantity: str | None = Field(
        description=(
            "String with units where the preferences state a specific amount "
            '(e.g. "2 gallons"); null where discretionary.'
        )
    )

class MealPlan(BaseModel):
    meals: list[Meal]
    other_items: list[OtherItem]
    notes: str = Field(
        description=(
            "Short plain-language paragraph explaining why this set of meals "
            "was chosen: what the feedback and pantry state guided you toward, "
            "what you avoided and why, and how the rotation was handled."
        )
    )

# ── Tool definition ───────────────────────────────────────────────────────────

def build_tool() -> dict:
    """Derive the tool schema from the Pydantic model so the two cannot drift."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Submit the completed weekly meal plan. This is the only way to "
            "return a result; call it exactly once."
        ),
        "input_schema": MealPlan.model_json_schema(),
    }

# ── Helpers ───────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent

def read_text(filename: str) -> str | None:
    p = BASE / filename
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")

def read_json(filename: str) -> dict | list | None:
    p = BASE / filename
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))

# ── Build user message ────────────────────────────────────────────────────────

def build_user_message() -> str:
    parts = []

    preferences = read_text("preferences.md")
    if preferences:
        parts.append(f"## preferences.md\n\n{preferences}")
    else:
        print("[warn] preferences.md not found")

    pantry = read_json("pantry_snapshot.json")
    if pantry is not None:
        parts.append(f"## pantry_snapshot.json\n\n{json.dumps(pantry, indent=2)}")
    else:
        print("[warn] pantry_snapshot.json not found — telling model to assume fully stocked")
        parts.append("## pantry_snapshot.json\n\nFile not present. Assume all common staples are in stock.")

    feedback = read_json("feedback.json")
    if feedback is not None:
        parts.append(f"## feedback.json\n\n{json.dumps(feedback, indent=2)}")
    else:
        print("[warn] feedback.json not found — no history to work from")
        parts.append("## feedback.json\n\nNo feedback history yet.")

    parts.append(
        f"Generate this week's meal plan now by calling the {TOOL_NAME} tool."
    )
    return "\n\n---\n\n".join(parts)

# ── Response extraction ───────────────────────────────────────────────────────

def extract_plan_input(response) -> dict:
    """Pull the tool_use input off the response.

    Never index content[0] directly — block order is not guaranteed, and any
    text block the model emits alongside the tool call would break that.
    """
    for block in response.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            return block.input

    # Should not happen with tool_choice forced, but fail loudly if it does.
    print(f"\n[error] No {TOOL_NAME} tool call in response.")
    print(f"  stop_reason: {response.stop_reason}")
    for block in response.content:
        detail = getattr(block, "text", None) or getattr(block, "name", "")
        print(f"  block: {block.type} {detail!r}")
    if response.stop_reason == "max_tokens":
        print(
            f"\n  Hint: hit the {MAX_TOKENS} token cap mid-call. "
            "Raise MAX_TOKENS and retry."
        )
    sys.exit(1)

# ── Guards and checks ─────────────────────────────────────────────────────────

def check_previous_plan_recorded() -> None:
    """Refuse to overwrite a meal_plan.json that feedback.py hasn't recorded.

    meal_plan.json is the only copy of the plan until feedback.py appends it
    to feedback.json, so overwriting it first loses that week from history.
    """
    plan_file = BASE / "meal_plan.json"
    if not plan_file.exists():
        return
    plan = feedback.load_meal_plan(plan_file)
    history = feedback.load_feedback(feedback.FEEDBACK_FILE)
    if feedback.already_recorded(history, feedback.build_week(plan)):
        return
    print(
        "[error] meal_plan.json holds a plan that isn't in feedback.json yet.\n"
        "        Run feedback.py to record it first, or pass --force to "
        "overwrite it."
    )
    sys.exit(1)

def warn_on_plan_issues(plan: MealPlan) -> None:
    """Print warnings for invariant violations the schema can't express."""
    pantry_keys = set(pantry.load_pantry())
    warnings = []

    if len(plan.meals) != EXPECTED_MEALS:
        warnings.append(f"{len(plan.meals)} meals planned, expected {EXPECTED_MEALS}")

    for meal in plan.meals:
        for ing in meal.ingredients:
            name = pantry._normalize(ing.name)
            where = f"'{ing.name}' in {meal.name}"
            if ing.is_staple and ing.quantity is not None:
                warnings.append(f"{where}: staple with a quantity ({ing.quantity})")
            if not ing.is_staple and ing.quantity is None:
                warnings.append(f"{where}: fresh ingredient with no quantity")
            if ing.is_staple and name not in pantry_keys:
                warnings.append(f"{where}: staple isn't a pantry key (naming drift?)")

    for w in warnings:
        print(f"[warn] {w}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="meal_planner.py",
        description="Generate this week's meal plan into meal_plan.json.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite meal_plan.json even if feedback.py hasn't recorded it",
    )
    args = parser.parse_args()

    if not args.force:
        check_previous_plan_recorded()

    system_prompt = read_text("meal_planner_prompt.md")
    if not system_prompt:
        print("[error] meal_planner_prompt.md not found — cannot continue")
        sys.exit(1)

    # build pantry snapshot
    pantry_snapshot.main()
    user_message = build_user_message()

    client = anthropic.Anthropic()
    print("Calling Claude API…")

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        tools=[build_tool()],
        tool_choice={"type": "tool", "name": TOOL_NAME},
    )

    data = extract_plan_input(response)

    # The API validates against the tool schema, but it is not a hard
    # guarantee — revalidate locally so downstream code can trust the shape.
    try:
        plan = MealPlan.model_validate(data)
    except ValidationError as e:
        print(f"\n[error] Tool input didn't match expected schema:\n{e}")
        print("\nRaw tool input:\n", json.dumps(data, indent=2))
        sys.exit(1)

    # Save full output. Dump from the validated model so the file always
    # reflects the schema rather than whatever extra keys came back.
    out = BASE / "meal_plan.json"
    out.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    print(f"Saved → {out}\n")
    warn_on_plan_issues(plan)

    # Print readable summary
    print("=" * 52)
    print("MEALS THIS WEEK")
    print("=" * 52)
    for meal in plan.meals:
        cal = f"  (~{meal.estimated_calories} kcal/serving)" if meal.estimated_calories else ""
        print(f"\n  {meal.name}{cal}")
        for ing in meal.ingredients:
            qty = f" — {ing.quantity}" if ing.quantity else ""
            tag = "  [staple]" if ing.is_staple else ""
            print(f"    • {ing.name}{qty}{tag}")

    print("\n" + "=" * 52)
    print("OTHER ITEMS")
    print("=" * 52)
    for item in plan.other_items:
        qty = f" — {item.quantity}" if item.quantity else ""
        print(f"  • {item.name}{qty}")

    print("\n" + "=" * 52)
    print("NOTES")
    print("=" * 52)
    print(f"  {plan.notes}\n")

if __name__ == "__main__":
    main()
