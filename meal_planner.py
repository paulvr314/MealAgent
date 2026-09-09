#!/usr/bin/env python3
"""
MealAgent — Meal Planner
Run: python meal_planner.py
"""

import json
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

import pantry_snapshot

load_dotenv()

# ── Pydantic models ───────────────────────────────────────────────────────────

class Ingredient(BaseModel):
    name: str
    quantity: str | None
    is_staple: bool

class Meal(BaseModel):
    name: str
    estimated_calories: int | None = None
    servings: int
    ingredients: list[Ingredient]

class OtherItem(BaseModel):
    name: str
    quantity: str | None

class MealPlan(BaseModel):
    meals: list[Meal]
    other_items: list[OtherItem]
    notes: str

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

    parts.append("Generate this week's meal plan now.")
    return "\n\n---\n\n".join(parts)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    system_prompt = read_text("meal_planner_prompt.md")
    if not system_prompt:
        print("[error] meal_planner_prompt.md not found — cannot continue")
        sys.exit(1)

    #build pantry snapshot
    pantry_snapshot.main()
    user_message = build_user_message()

    client = anthropic.Anthropic()
    print("Calling Claude API…")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if the model included them despite instructions
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Parse and validate
    try:
        data = json.loads(raw)
        plan = MealPlan.model_validate(data)
    except json.JSONDecodeError as e:
        print(f"\n[error] Response was not valid JSON:\n{e}")
        print("\nRaw response:\n", raw)
        sys.exit(1)
    except ValidationError as e:
        print(f"\n[error] JSON didn't match expected schema:\n{e}")
        print("\nRaw response:\n", raw)
        sys.exit(1)

    # Save full output
    out = BASE / "meal_plan.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Saved → {out}\n")

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