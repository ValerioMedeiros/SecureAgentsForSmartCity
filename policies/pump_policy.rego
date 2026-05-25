package smartcity

import rego.v1

default allow := {
  "allowed": false,
  "risk_level": "high",
  "approval_mode": "deny",
  "verdict_color": "red",
  "reason": "Denied by default"
}

contains_pump_action if {
  some step in input.plan.steps
  step.action == "turnOffPump"
}

contains_pump_action if {
  some step in input.plan.steps
  step.action == "turnOnPump"
}

allow := {
  "allowed": true,
  "risk_level": "low",
  "approval_mode": "auto",
  "verdict_color": "green",
  "reason": "Plan allowed for no pump control actions."
} if {
  not contains_pump_action
}

allow := {
  "allowed": false,
  "risk_level": "medium",
  "approval_mode": "human",
  "verdict_color": "yellow",
  "reason": "Plan includes pump control actions, requires human approval"
} if {
  contains_pump_action
}
