"""Security / authorization manager.

Implements the authorization side of the IAM Generic Enabler: it turns a
verified identity (a Keycloak access token, or a static PoC token) into the set
of actions that identity is permitted to perform, and validates whether a
candidate plan is fully covered by those permissions.

Keycloak realm roles map to action permissions as follows:

    pump_admin     -> turnOnPump, turnOffPump, notifyUser
    pump_operator  -> turnOnPump, turnOffPump
    viewer         -> notifyUser

When Keycloak is disabled or unreachable the manager falls back to a small set
of static tokens so the PoC remains runnable offline.
"""

from __future__ import annotations

from typing import List, Optional

from ..infra import keycloak_auth
from .models import ActionType, CandidatePlan, User

# Realm role -> permitted actions. Single source of truth shared by the
# Keycloak-backed and the static authorization paths.
ROLE_PERMISSIONS = {
    "pump_admin": [
        ActionType.TURN_ON_PUMP,
        ActionType.TURN_OFF_PUMP,
        ActionType.NOTIFY_USER,
    ],
    "pump_operator": [ActionType.TURN_ON_PUMP, ActionType.TURN_OFF_PUMP],
    "viewer": [ActionType.NOTIFY_USER],
}


def permissions_for_roles(roles) -> List[ActionType]:
    """Aggregate the permitted actions for a set of realm roles."""
    permissions: List[ActionType] = []
    for role in roles:
        for action in ROLE_PERMISSIONS.get(role, []):
            if action not in permissions:
                permissions.append(action)
    return permissions


class AuthorizedIdentity:
    """A verified identity and the actions it is permitted to perform."""

    def __init__(self, username: str, permissions: List[ActionType], source: str):
        self.username = username
        self.permissions = permissions
        self.source = source  # "keycloak" | "static"

    def can(self, action: ActionType) -> bool:
        return action in self.permissions

    def can_all(self, actions) -> bool:
        return all(self.can(a) for a in actions)


class SecurityManager:
    def __init__(self):
        # Static identities mirror the Keycloak users/roles and are used as an
        # offline fallback when Keycloak is disabled or unreachable.
        self.users = [
            User(
                username="admin",
                token="token123",
                permissions=ROLE_PERMISSIONS["pump_admin"],
            ),
            User(
                username="operator",
                token="token456",
                permissions=ROLE_PERMISSIONS["pump_operator"],
            ),
            User(
                username="viewer",
                token="token789",
                permissions=ROLE_PERMISSIONS["viewer"],
            ),
        ]

    # ── Identity resolution ───────────────────────────────────────────

    def resolve_identity(self, token: str) -> Optional[AuthorizedIdentity]:
        """Resolve a bearer token into a verified identity.

        Tries Keycloak first (when enabled); falls back to the static PoC
        tokens. Returns ``None`` when the token cannot be authenticated.
        """
        if not token:
            return None

        if keycloak_auth.is_enabled():
            claims = keycloak_auth.validate_token(token)
            if claims is not None:
                roles = keycloak_auth.realm_roles(claims)
                return AuthorizedIdentity(
                    username=keycloak_auth.username_from_claims(claims),
                    permissions=permissions_for_roles(roles),
                    source="keycloak",
                )

        for user in self.users:
            if user.token == token:
                return AuthorizedIdentity(
                    username=user.username,
                    permissions=list(user.permissions),
                    source="static",
                )
        return None

    # ── Plan authorization ─────────────────────────────────────────────

    def authorize_plan(self, token: str, plan: CandidatePlan) -> bool:
        identity = self.resolve_identity(token)
        if identity is None:
            return False
        return identity.can_all(step.action for step in plan.steps)

    def get_authorized_users(self, action: ActionType) -> List[str]:
        authorized_users = []
        for user in self.users:
            if action in user.permissions:
                authorized_users.append(user.username)
        return authorized_users


security_manager = SecurityManager()
