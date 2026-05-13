# LLM Planning with LangChain - Implementation Guide

## Overview

The LLM planning system uses LangChain to integrate large language models (like GPT-4) with the smart city traffic management system. This enables intelligent, context-aware planning for traffic signal management in response to real-time events.

## Architecture

```
MonitorEvent (traffic/weather data)
        ↓
    planner.build_candidate_plan()
        ↓
    _llm_planner_payload()
        ↓
    generate_plan_with_llm() [NEW]
        ↓
    ChatOpenAI (LangChain)
        ↓
    Structured Plan (JSON)
        ↓
    validate_plan_dict()
        ↓
    CandidatePlan (Pydantic model)
```

## Features

### 1. **LangChain Integration**
   - Uses `langchain-openai.ChatOpenAI` for LLM interactions
   - Supports configurable models (GPT-4, GPT-4-turbo, GPT-3.5-turbo)
   - Temperature control for determinism vs. creativity

### 2. **Intelligent Prompting**
   - Context-aware prompt templates
   - Schema specification to ensure valid plan generation
   - Action enumeration for LLM reference

### 3. **Fallback Strategy**
   - Primary: LangChain-based LLM planning
   - Secondary: Legacy `LLM_PLAN_JSON` environment variable support
   - Tertiary: Deterministic rule-based planner

### 4. **Error Handling**
   - Graceful degradation if LLM unavailable
   - JSON parsing with fallback
   - Plan validation before acceptance
   - Comprehensive logging for debugging

## Configuration

### Environment Variables

```bash
# Enable LLM planner
export LLM_PLANNER_ENABLED=true

# OpenAI credentials
export OPENAI_API_KEY=sk-...

# Model selection
export OPENAI_MODEL=gpt-4-turbo

# LLM parameters
export LLM_TEMPERATURE=0.3  # Lower = more deterministic

# Traffic signal entity
export TRAFFIC_SIGNAL_ID=TrafficSignal:001
```

### See `.env.example` for complete configuration template

## Usage

### Basic Usage

```python
from smartcity.core.models import MonitorEvent
from smartcity.core.planner import build_candidate_plan

# Create an event
event = MonitorEvent(
    event_type="ambulance-emergency",
    ambulance_detected=True,
    heavy_rain=False,
    flood_risk=False,
    crowd_level="normal",
    location="Avenue 1"
)

# Generate plan (LLM or deterministic fallback)
plan = build_candidate_plan(event, trace_id="trace-001")

# Use plan
print(f"Plan: {plan.plan_id}")
print(f"Goal: {plan.goal}")
print(f"Risk: {plan.risk_level.value}")
print(f"Steps: {len(plan.steps)}")
```

### With Explicit LLM Planning

```python
from smartcity.core.llm_planner import generate_plan_with_llm

# Direct LLM planning
plan_dict = generate_plan_with_llm(event, trace_id="trace-001")

if plan_dict:
    print("LLM plan generated successfully")
else:
    print("LLM planning failed, using fallback")
```

## Plan Structure

The LLM generates plans matching this Pydantic schema:

```python
{
    "plan_id": "uuid",
    "goal": "Create emergency corridor for ambulance",
    "scenario": "ambulance-only",
    "risk_level": "high",  # low, medium, high
    "steps": [
        {
            "id": "read-state",
            "action": "getTrafficSignalState",
            "params": {"entity_id": "TrafficSignal:001"}
        },
        {
            "id": "set-priority",
            "action": "setPriorityCorridor",
            "params": {"entity_id": "TrafficSignal:001", "value": "emergency"}
        },
        {
            "id": "notify",
            "action": "notifyTrafficAgents",
            "params": {"message": "Emergency corridor activated for ambulance"}
        }
    ],
    "approval": {
        "autonomy_level": 3  # 1=auto, 2=human, 3=human-required
    },
    "telemetry": {
        "traceId": "trace-001"
    }
}
```

## Prompt Engineering

The LLM receives:

1. **Event Data**: Current traffic/weather conditions
2. **Available Actions**: List of possible traffic interventions
3. **Schema Example**: Reference implementation showing expected output format
4. **Instructions**: Clear guidelines for plan generation

This multi-faceted approach ensures the LLM understands:
- What problem to solve (event context)
- What tools are available (actions)
- How to format the response (schema)
- Expected behavior (instructions)

## Risk Level Determination

The LLM assigns risk levels based on event severity:

- **LOW**: Normal conditions, light rain → autonomy_level=1 (auto-approve)
- **MEDIUM**: Heavy rain, high crowd → autonomy_level=2 (human review)
- **HIGH**: Flood risk, ambulance detected → autonomy_level=3 (requires human approval)

## Logging

All LLM planner operations are logged with trace IDs for observability:

```
[planner] INFO: LLM plan generated and validated successfully
  traceId: trace-001
  plan_id: uuid-12345
  scenario: ambulance-only
  risk_level: high
```

## Testing

Run the example script:

```bash
python src/smartcity/app/examples_llm_planner.py
```

This demonstrates:
- ✓ Basic ambulance detection
- ✓ Flood response planning
- ✓ Combined emergency scenarios
- ✓ Normal operation baselines

## Troubleshooting

### LLM Planner Disabled
```
OPENAI_API_KEY not set; LLM planner unavailable
```
→ Set `OPENAI_API_KEY` environment variable

### LLM Response Parsing Failed
```
Failed to parse LLM response as JSON
```
→ Check model output format; may need prompt adjustment

### Plan Validation Failed
```
Generated plan failed validation: ...
```
→ LLM output doesn't match schema; check prompt instructions

### API Timeout
```
Error during LLM plan generation: ...
```
→ Check OpenAI API availability and rate limits

## Performance Considerations

- **Latency**: LLM calls add ~500ms-2s to plan generation
- **Cost**: Each LLM call incurs OpenAI API charges
- **Fallback**: System uses deterministic planner if LLM unavailable
- **Caching**: Consider caching plans for identical events

## Future Enhancements

1. **Prompt Optimization**: Fine-tune prompt templates based on performance
2. **Caching Layer**: Cache LLM responses for similar events
3. **Fine-tuning**: Train custom models on domain-specific traffic data
4. **Multi-step Planning**: Generate longer, more sophisticated plans
5. **Cost Optimization**: Use cheaper models (GPT-3.5-turbo) with prompt engineering
6. **Chain-of-Thought**: Request reasoning before plan generation for better quality

## Files

- `llm_planner.py`: Core LLM planning implementation
- `planner.py`: Integration point with fallback strategy
- `examples_llm_planner.py`: Example usage and testing
- `.env.example`: Configuration template

## References

- [LangChain Documentation](https://python.langchain.com/)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)
- [Smart City Architecture](../docs/IMPLEMENTATION_NOTES.md)
