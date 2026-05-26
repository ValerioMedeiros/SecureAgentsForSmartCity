from typing import List

from .models import ActionType, CandidatePlan, User


class SecurityManager:
    def __init__(self):
        self.users = [
            User(
                username="admin",
                token="token123",
                permissions=[
                    ActionType.TURN_OFF_PUMP,
                    ActionType.TURN_ON_PUMP,
                    ActionType.NOTIFY_USER,
                ],
            ),
            User(
                username="operator",
                token="token456",
                permissions=[ActionType.TURN_OFF_PUMP, ActionType.TURN_ON_PUMP],
            ),
            User(
                username="viewer",
                token="token789",
                permissions=[ActionType.NOTIFY_USER],
            ),
        ]

    def authorize_plan(self, token: str, plan: CandidatePlan) -> bool:
        for user in self.users:
            if user.token == token:
                for step in plan.steps:
                    if step.action not in user.permissions:
                        return False
                return True
        else:
            return False
        
    def get_authorized_users(self, action: ActionType) -> List[str]:
        authorized_users = []
        for user in self.users:
            if action in user.permissions:
                authorized_users.append(user.username)
        return authorized_users

security_manager = SecurityManager()