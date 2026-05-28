from dataclasses import dataclass


@dataclass(frozen=True)
class MacroTargets:
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int


def calculate_bmr(
    age: int | None,
    weight_kg: float | None,
    height_cm: float | None,
    sex: str | None = None,
) -> float:
    if not age or not weight_kg or not height_cm:
        return 2000

    sex_normalized = (sex or "male").lower()
    offset = -161 if sex_normalized in {"female", "woman", "f"} else 5
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + offset


def calculate_tdee(
    age: int | None,
    weight_kg: float | None,
    height_cm: float | None,
    activity_level: str = "moderate",
    sex: str | None = None,
) -> float:
    multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "active": 1.725,
        "very_active": 1.9,
    }
    return calculate_bmr(age, weight_kg, height_cm, sex) * multipliers.get(
        activity_level, 1.55
    )


def calculate_macro_targets(
    age: int | None,
    weight_kg: float | None,
    height_cm: float | None,
    activity_level: str,
    goal: str | None,
    sex: str | None = None,
) -> MacroTargets:
    tdee = calculate_tdee(age, weight_kg, height_cm, activity_level, sex)
    goal_normalized = (goal or "maintenance").lower()

    if goal_normalized in {"cut", "fat_loss", "lose_weight", "减脂"}:
        calories = tdee * 0.82
    elif goal_normalized in {"bulk", "muscle_gain", "增肌"}:
        calories = tdee * 1.1
    else:
        calories = tdee

    protein_ratio = 0.3
    carbs_ratio = 0.4
    fat_ratio = 0.3
    return MacroTargets(
        calories=round(calories),
        protein_g=round((calories * protein_ratio) / 4),
        carbs_g=round((calories * carbs_ratio) / 4),
        fat_g=round((calories * fat_ratio) / 9),
    )


def adjustment_multiplier(
    fatigue: int | None,
    soreness: int | None,
    sleep_hours: float | None,
    completion: int | None,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    multiplier = 1.0

    if fatigue and fatigue >= 8:
        multiplier -= 0.2
        reasons.append("fatigue is high")
    if soreness and soreness >= 8:
        multiplier -= 0.15
        reasons.append("soreness is high")
    if sleep_hours is not None and sleep_hours < 6:
        multiplier -= 0.15
        reasons.append("sleep is below 6 hours")
    if completion is not None and completion < 60:
        multiplier -= 0.1
        reasons.append("recent completion is low")
    if completion is not None and completion > 90 and fatigue and fatigue <= 5:
        multiplier += 0.05
        reasons.append("completion is strong and fatigue is manageable")

    return max(0.55, min(1.1, multiplier)), reasons
