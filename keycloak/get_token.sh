#!/usr/bin/env bash
# Fetch a Keycloak access token for a smartcity realm user (direct access grant).
#
# Usage:
#   ./keycloak/get_token.sh [username] [password]
#
# Defaults to operator/operator123. Useful for smoke-testing the IAM integration
# and for calling protected endpoints with a Bearer token.
#
# Test users (provisioned by realm-export.json):
#   admin    / admin123     -> pump_admin    (turnOnPump, turnOffPump, notifyUser)
#   operator / operator123  -> pump_operator (turnOnPump, turnOffPump)
#   viewer   / viewer123    -> viewer        (notifyUser)

set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8090}"
REALM="${KEYCLOAK_REALM:-smartcity}"
CLIENT_ID="${KEYCLOAK_CLIENT_ID:-smartcity-poc}"
CLIENT_SECRET="${KEYCLOAK_CLIENT_SECRET:-smartcity-poc-secret}"
USERNAME="${1:-operator}"
PASSWORD="${2:-operator123}"

TOKEN_ENDPOINT="${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token"

ACCESS_TOKEN=$(curl -sf -X POST "${TOKEN_ENDPOINT}" \
  -d "grant_type=password" \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  -d "username=${USERNAME}" \
  -d "password=${PASSWORD}" \
  -d "scope=openid" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "${ACCESS_TOKEN}"
