package smartcity

import rego.v1

default allow := {
  "allowed": false,
  "risk_level": "high",
  "approval_mode": "deny",
  "verdict_color": "red",
  "reason": "Denied by default"
}

allow := {
  "allowed": true,
  "risk_level": "low",
  "approval_mode": "auto",
  "verdict_color": "green",
  "reason": "Low risk auto-approved"
} if {
  input.token == input.expected_user_token
  risk := lower(input.plan.risk_level)
  risk == "low"
}

allow := {
  "allowed": true,
  "risk_level": "medium",
  "approval_mode": "human",
  "verdict_color": "yellow",
  "reason": "Medium risk approved with human token"
} if {
  input.token == input.expected_user_token
  risk := lower(input.plan.risk_level)
  risk == "medium"
  input.plan.approval.human_token == input.expected_human_token
}

allow := {
  "allowed": true,
  "risk_level": "medium",
  "approval_mode": "auto",
  "verdict_color": "yellow",
  "reason": "Medium risk auto-approved"
} if {
  input.token == input.expected_user_token
  risk := lower(input.plan.risk_level)
  risk == "medium"
}

allow := {
  "allowed": false,
  "risk_level": "high",
  "approval_mode": "human",
  "verdict_color": "yellow",
  "reason": "High risk needs approval"
} if {
  input.token == input.expected_user_token
  risk := lower(input.plan.risk_level)
  risk == "high"
}

allow := {
  "allowed": false,
  "risk_level": lower(input.plan.risk_level),
  "approval_mode": "deny",
  "verdict_color": "red",
  "reason": "Invalid user token"
} if {
  input.token != input.expected_user_token
}
