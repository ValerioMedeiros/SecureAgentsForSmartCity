"""
Example usage of the LLM planner with LangChain.

This script demonstrates how to configure and use the LLM planning system
for intelligent traffic management in a smart city.
"""

import json
import os

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from smartcity.core.models import MonitorEvent
from smartcity.core.planner import build_candidate_plan
from smartcity.infra.logging_utils import configure_logger

logger = configure_logger("example_llm_planner")


def example_1_basic_llm_planning():
    """Example 1: Basic LLM planning with an ambulance event."""
    print("\n" + "=" * 60)
    print("Example 1: Ambulance Detection with LLM Planning")
    print("=" * 60)

    event = MonitorEvent(
        event_type="ambulance-emergency",
        ambulance_detected=True,
        heavy_rain=False,
        flood_risk=False,
        crowd_level="normal",
        location="Avenue 1 near Hospital",
        notes="Ambulance approaching intersection",
    )

    try:
        plan = build_candidate_plan(event, trace_id="example-001")
        print(f"✓ Plan generated: {plan.plan_id}")
        print(f"  Goal: {plan.goal}")
        print(f"  Scenario: {plan.scenario}")
        print(f"  Risk Level: {plan.risk_level.value}")
        print(f"  Autonomy Level: {plan.approval.autonomy_level}")
        print(f"  Steps: {len(plan.steps)}")
        for i, step in enumerate(plan.steps, 1):
            print(f"    {i}. {step.action.value} ({step.id})")
    except Exception as e:
        print(f"✗ Error generating plan: {e}")


def example_2_flood_response():
    """Example 2: Flood risk scenario with LLM planning."""
    print("\n" + "=" * 60)
    print("Example 2: Flood Risk with LLM Planning")
    print("=" * 60)

    event = MonitorEvent(
        event_type="weather-flood",
        ambulance_detected=False,
        heavy_rain=True,
        flood_risk=True,
        crowd_level="high",
        location="Downtown District",
        notes="Heavy rainfall detected, flood risk rising",
    )

    try:
        plan = build_candidate_plan(event, trace_id="example-002")
        print(f"✓ Plan generated: {plan.plan_id}")
        print(f"  Goal: {plan.goal}")
        print(f"  Scenario: {plan.scenario}")
        print(f"  Risk Level: {plan.risk_level.value}")
        print(f"  Autonomy Level: {plan.approval.autonomy_level}")
        print(f"  Steps: {len(plan.steps)}")
        for i, step in enumerate(plan.steps, 1):
            print(f"    {i}. {step.action.value} ({step.id})")
    except Exception as e:
        print(f"✗ Error generating plan: {e}")


def example_3_combined_scenario():
    """Example 3: Combined emergency (ambulance + flood) scenario."""
    print("\n" + "=" * 60)
    print("Example 3: Combined Scenario (Ambulance + Flood)")
    print("=" * 60)

    event = MonitorEvent(
        event_type="combined",
        ambulance_detected=True,
        heavy_rain=True,
        flood_risk=True,
        crowd_level="dense",
        location="Main Street",
        notes="Ambulance emergency during heavy rain and flooding",
    )

    try:
        plan = build_candidate_plan(event, trace_id="example-003")
        print(f"✓ Plan generated: {plan.plan_id}")
        print(f"  Goal: {plan.goal}")
        print(f"  Scenario: {plan.scenario}")
        print(f"  Risk Level: {plan.risk_level.value}")
        print(f"  Autonomy Level: {plan.approval.autonomy_level}")
        print(f"  Steps: {len(plan.steps)}")
        for i, step in enumerate(plan.steps, 1):
            print(f"    {i}. {step.action.value} ({step.id})")
            params_str = json.dumps(step.params, indent=6)
            print(f"       Params: {params_str}")
    except Exception as e:
        print(f"✗ Error generating plan: {e}")


def example_4_normal_operation():
    """Example 4: Normal operation (no incidents)."""
    print("\n" + "=" * 60)
    print("Example 4: Normal Traffic Operation")
    print("=" * 60)

    event = MonitorEvent(
        event_type="baseline",
        ambulance_detected=False,
        heavy_rain=False,
        flood_risk=False,
        crowd_level="normal",
        location="City Center",
        notes="Standard traffic flow",
    )

    try:
        plan = build_candidate_plan(event, trace_id="example-004")
        print(f"✓ Plan generated: {plan.plan_id}")
        print(f"  Goal: {plan.goal}")
        print(f"  Scenario: {plan.scenario}")
        print(f"  Risk Level: {plan.risk_level.value}")
        print(f"  Autonomy Level: {plan.approval.autonomy_level}")
        print(f"  Steps: {len(plan.steps)}")
        for i, step in enumerate(plan.steps, 1):
            print(f"    {i}. {step.action.value} ({step.id})")
    except Exception as e:
        print(f"✗ Error generating plan: {e}")


def print_configuration_info():
    """Print current LLM planner configuration."""
    print("\n" + "=" * 60)
    print("LLM Planner Configuration")
    print("=" * 60)

    llm_enabled = os.getenv("LLM_PLANNER_ENABLED", "false").lower() == "true"
    openai_key_set = bool(os.getenv("OPENAI_API_KEY", ""))
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4-turbo")
    llm_temp = os.getenv("LLM_TEMPERATURE", "0.3")

    print(f"LLM Planner Enabled: {llm_enabled}")
    print(f"OpenAI API Key Set: {openai_key_set}")
    print(f"OpenAI Model: {openai_model}")
    print(f"LLM Temperature: {llm_temp}")
    print(f"\nNote: Set LLM_PLANNER_ENABLED=true to enable LLM-based planning")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("LLM Planner Examples - Smart City Traffic Management")
    print("=" * 60)

    print_configuration_info()

    # Run examples
    # Note: These will use deterministic planner by default unless LLM is configured
    example_1_basic_llm_planning()
    example_2_flood_response()
    example_3_combined_scenario()
    example_4_normal_operation()

    print("\n" + "=" * 60)
    print("Examples Complete")
    print("=" * 60)
