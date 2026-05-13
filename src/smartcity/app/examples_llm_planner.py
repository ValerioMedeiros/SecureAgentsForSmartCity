"""
Example usage of the LLM planner with LangChain.

This script demonstrates how to configure and use the LLM planning system
for intelligent traffic management in a smart city.

Set EXECUTE_PLANS=true to also execute the generated plans.
"""

import json
import os
import uuid

from dotenv import load_dotenv

from ..core.executor import execute_candidate_plan
from ..core.models import MonitorEvent
from ..core.planner import build_candidate_plan
from ..infra.logging_utils import configure_logger
from .init_traffic_signal import main as initialize_traffic_signal

# Load environment variables
load_dotenv()

logger = configure_logger("example_llm_planner")

EXECUTE_PLANS = os.getenv("EXECUTE_PLANS", "false").lower() == "true"


def _print_plan_details(plan, title: str = "Plan Details"):
    """Print detailed plan information."""
    print(f"\n{title}:")
    print(f"  Plan ID: {plan.plan_id}")
    print(f"  Goal: {plan.goal}")
    print(f"  Scenario: {plan.scenario}")
    print(f"  Risk Level: {plan.risk_level.value}")
    print(f"  Autonomy Level: {plan.approval.autonomy_level}")
    print(f"  Steps: {len(plan.steps)}")
    for i, step in enumerate(plan.steps, 1):
        print(f"    {i}. {step.action.value} ({step.id})")


def _print_execution_results(report):
    """Print execution report details."""
    print(f"\n  Execution Report:")
    print(f"    Status: {'✓ EXECUTED' if report.executed else '✗ BLOCKED'}")
    print(f"    Policy Source: {report.policy.source}")
    print(
        f"    Policy Mode: {report.policy.approval_mode.value} ({report.policy.verdict_color})"
    )
    print(f"    Reason: {report.policy.reason}")
    if report.step_results:
        print(f"    Steps Executed: {len(report.step_results)}")
        for result in report.step_results:
            status = "✓" if result.status_code < 400 else "✗"
            print(
                f"      {status} {result.step_id}: {result.action.value} [{result.status_code}]"
            )


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
        trace_id = str(uuid.uuid4())
        plan = build_candidate_plan(event, trace_id=trace_id)
        print(f"✓ Plan generated: {plan.plan_id}")
        _print_plan_details(plan)

        if EXECUTE_PLANS:
            report = execute_candidate_plan(plan)
            _print_execution_results(report)
    except Exception as e:
        print(f"✗ Error: {e}")


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
        trace_id = str(uuid.uuid4())
        plan = build_candidate_plan(event, trace_id=trace_id)
        print(f"✓ Plan generated: {plan.plan_id}")
        _print_plan_details(plan)

        if EXECUTE_PLANS:
            report = execute_candidate_plan(plan)
            _print_execution_results(report)
    except Exception as e:
        print(f"✗ Error: {e}")


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
        trace_id = str(uuid.uuid4())
        plan = build_candidate_plan(event, trace_id=trace_id)
        print(f"✓ Plan generated: {plan.plan_id}")
        _print_plan_details(plan)

        if EXECUTE_PLANS:
            report = execute_candidate_plan(plan)
            _print_execution_results(report)
    except Exception as e:
        print(f"✗ Error: {e}")


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
        trace_id = str(uuid.uuid4())
        plan = build_candidate_plan(event, trace_id=trace_id)
        print(f"✓ Plan generated: {plan.plan_id}")
        _print_plan_details(plan)

        if EXECUTE_PLANS:
            report = execute_candidate_plan(plan)
            _print_execution_results(report)
    except Exception as e:
        print(f"✗ Error: {e}")


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
    print(f"Execute Plans: {EXECUTE_PLANS}")
    print("\nNote: Set LLM_PLANNER_ENABLED=true to enable LLM-based planning")
    if EXECUTE_PLANS:
        print("Note: Set EXECUTE_PLANS=false to disable plan execution")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("LLM Planner Examples - Smart City Traffic Management")
    print("=" * 60)

    print_configuration_info()

    # Initialize traffic signal if execution is enabled
    if EXECUTE_PLANS:
        print("\nInitializing TrafficSignal for execution...")
        try:
            initialize_traffic_signal()
            print("✓ TrafficSignal initialized")
        except Exception as e:
            print(f"✗ Failed to initialize TrafficSignal: {e}")
            print("  Continuing with examples (execution may fail)")

    # Run examples
    # Note: These will use deterministic planner by default unless LLM is configured
    example_1_basic_llm_planning()
    example_2_flood_response()
    example_3_combined_scenario()
    example_4_normal_operation()

    print("\n" + "=" * 60)
    print("Examples Complete")
    print("=" * 60)
