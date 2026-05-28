from fast_api.app.services.fitness_math import (
    adjustment_multiplier,
    calculate_macro_targets,
)


def test_macro_targets_for_muscle_gain_are_plausible():
    targets = calculate_macro_targets(
        age=28,
        weight_kg=72,
        height_cm=175,
        activity_level="moderate",
        goal="muscle_gain",
        sex="male",
    )

    assert targets.calories > 2500
    assert targets.protein_g > 150
    assert targets.carbs_g > targets.fat_g


def test_adjustment_multiplier_reduces_volume_for_poor_recovery():
    multiplier, reasons = adjustment_multiplier(
        fatigue=9,
        soreness=8,
        sleep_hours=5.5,
        completion=50,
    )

    assert multiplier < 0.8
    assert "fatigue is high" in reasons
    assert "sleep is below 6 hours" in reasons
