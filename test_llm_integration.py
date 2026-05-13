"""Quick integration test for LLM planner implementation."""

import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from smartcity.core.models import MonitorEvent
from smartcity.core.planner import build_candidate_plan


def test_llm_planner_integration():
    """Test that LLM planner integrates correctly with the planning pipeline."""
    print("\n" + "=" * 60)
    print("LLM Planner Integration Test")
    print("=" * 60)

    # Test 1: Verify imports
    print("\n[1] Testing imports...")
    try:
        from smartcity.core.llm_planner import (
            LLM_PLANNER_ENABLED,
            PLAN_GENERATION_PROMPT,
            generate_plan_with_llm,
        )

        print("    ✓ All imports successful")
    except ImportError as e:
        print(f"    ✗ Import failed: {e}")
        return False

    # Test 2: Verify configuration loading
    print("\n[2] Checking configuration...")
    print(f"    LLM_PLANNER_ENABLED: {LLM_PLANNER_ENABLED}")
    print(f"    LLM temperature: {os.getenv('LLM_TEMPERATURE', '0.3')}")
    print(f"    OpenAI model: {os.getenv('OPENAI_MODEL', 'gpt-4-turbo')}")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        print(f"    OpenAI API key: ***{api_key[-8:]}")
    else:
        print("    OpenAI API key: NOT SET (will use deterministic planner)")

    # Test 3: Create test event
    print("\n[3] Creating test event...")
    event = MonitorEvent(
        event_type="test-event",
        ambulance_detected=False,
        heavy_rain=False,
        flood_risk=False,
        crowd_level="normal",
        location="Test Location",
        notes="Test event for integration testing",
    )
    print(f"    ✓ Event created: {event.event_type}")

    # Test 4: Generate plan through full pipeline
    print("\n[4] Testing plan generation pipeline...")
    try:
        plan = build_candidate_plan(event, trace_id="test-integration-001")
        print(f"    ✓ Plan generated successfully")
        print(f"      Plan ID: {plan.plan_id}")
        print(f"      Scenario: {plan.scenario}")
        print(f"      Risk Level: {plan.risk_level.value}")
        print(f"      Autonomy Level: {plan.approval.autonomy_level}")
        print(f"      Steps: {len(plan.steps)}")

        # Verify plan structure
        assert plan.plan_id, "Plan ID is empty"
        assert plan.goal, "Plan goal is empty"
        assert len(plan.steps) > 0, "Plan has no steps"
        assert plan.approval.autonomy_level in [1, 2, 3], "Invalid autonomy level"
        print("    ✓ Plan structure validation passed")

    except Exception as e:
        print(f"    ✗ Plan generation failed: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Test 5: Test with different event types
    print("\n[5] Testing multiple event scenarios...")
    test_cases = [
        ("ambulance", {"ambulance_detected": True}),
        ("flood", {"flood_risk": True, "heavy_rain": True}),
        ("combined", {"ambulance_detected": True, "flood_risk": True}),
    ]

    for name, event_kwargs in test_cases:
        try:
            test_event = MonitorEvent(**event_kwargs)
            test_plan = build_candidate_plan(test_event, trace_id=f"test-{name}")
            print(f"    ✓ {name:12} scenario: risk={test_plan.risk_level.value}")
        except Exception as e:
            print(f"    ✗ {name:12} scenario failed: {e}")
            return False

    print("\n" + "=" * 60)
    print("✓ All integration tests passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = test_llm_planner_integration()
    sys.exit(0 if success else 1)
