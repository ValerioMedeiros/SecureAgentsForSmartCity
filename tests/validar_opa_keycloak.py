#!/usr/bin/env python3
"""Teste narrativo de integração para OPA e Keycloak.

Execute a partir da raiz do repositório:

    python tests/validar_opa_keycloak.py

Pré-requisito:

    docker compose up -d opa keycloak
"""

from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from smartcity.core.models import ActionType  # noqa: E402
from smartcity.core.security import permissions_for_roles  # noqa: E402


OPA_URL = os.getenv("OPA_URL", "http://localhost:8181").rstrip("/")
OPA_POLICY_PATH = os.getenv("OPA_POLICY_PATH", "v1/data/smartcity/allow").lstrip("/")
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8090").rstrip("/")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "smartcity")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "smartcity-poc")
KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "smartcity-poc-secret")
HTTP_TIMEOUT_SECONDS = float(os.getenv("TEST_HTTP_TIMEOUT_SECONDS", "5"))


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    expected: str
    actual: str
    error: str | None = None


def print_header(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def check_service_availability() -> bool:
    print_header("Verificando disponibilidade dos serviços")
    checks = [
        ("OPA", f"{OPA_URL}/health"),
        ("Keycloak", f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"),
    ]
    all_available = True

    for service_name, url in checks:
        try:
            response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            if response.ok:
                print(f"OK: {service_name} respondeu em {url}")
                continue
            all_available = False
            print(
                f"FALHOU: {service_name} respondeu HTTP {response.status_code} em {url}"
            )
        except requests.RequestException as exc:
            all_available = False
            print(f"FALHOU: {service_name} indisponível em {url}. Erro: {exc}")

    if not all_available:
        print(
            "\nErro: OPA e Keycloak precisam estar ativos antes da execução.\n"
            "Comando sugerido: docker compose up -d opa keycloak"
        )
    return all_available


def build_plan(action: str) -> dict[str, Any]:
    params_by_action = {
        "notifyUser": {"message": "Alerta de teste", "user_id": "citizen-001"},
        "turnOnPump": {"entity_id": "PumpDevice:001"},
        "turnOffPump": {"entity_id": "PumpDevice:001"},
    }
    return {
        "plan_id": f"test-{action}",
        "goal": "Validar política OPA",
        "scenario": "integration-test",
        "risk_level": "low",
        "steps": [
            {
                "id": "step-1",
                "action": action,
                "params": params_by_action[action],
            }
        ],
        "approval": {"autonomy_level": 1, "human_token": None},
        "telemetry": {"traceId": f"trace-{action}"},
    }


def evaluate_opa(action: str) -> dict[str, Any]:
    url = f"{OPA_URL}/{OPA_POLICY_PATH}"
    response = requests.post(
        url,
        json={"input": {"plan": build_plan(action)}},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def token_endpoint() -> str:
    return (
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    )


def login_keycloak(username: str, password: str) -> requests.Response:
    return requests.post(
        token_endpoint(),
        data={
            "grant_type": "password",
            "client_id": KEYCLOAK_CLIENT_ID,
            "client_secret": KEYCLOAK_CLIENT_SECRET,
            "username": username,
            "password": password,
            "scope": "openid",
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )


def decode_jwt_payload(access_token: str) -> dict[str, Any]:
    try:
        payload = access_token.split(".")[1]
    except IndexError as exc:
        raise ValueError("token JWT não possui três partes") from exc

    padded_payload = payload + "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(padded_payload.encode("utf-8"))
    return json.loads(decoded)


def roles_from_token(access_token: str) -> set[str]:
    claims = decode_jwt_payload(access_token)
    return set((claims.get("realm_access") or {}).get("roles") or [])


def permission_values_for_roles(roles: set[str]) -> set[str]:
    return {permission.value for permission in permissions_for_roles(roles)}


def format_mapping(values: dict[str, Any]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def opa_scenario(
    name: str,
    action: str,
    expected: dict[str, Any],
) -> ScenarioResult:
    result = evaluate_opa(action)
    actual = {
        "allowed": result.get("allowed"),
        "approval_mode": result.get("approval_mode"),
        "risk_level": result.get("risk_level"),
    }
    return ScenarioResult(
        name=name,
        passed=actual == expected,
        expected=format_mapping(expected),
        actual=format_mapping(actual),
    )


def keycloak_user_scenario(
    name: str,
    username: str,
    password: str,
    expected_role: str,
    expected_permissions: set[str],
) -> ScenarioResult:
    response = login_keycloak(username, password)
    response.raise_for_status()
    token = response.json()["access_token"]
    roles = roles_from_token(token)
    permissions = permission_values_for_roles(roles)
    actual = {
        "roles_contem": expected_role in roles,
        "permissoes": sorted(permissions),
    }
    expected = {
        "roles_contem": True,
        "permissoes": sorted(expected_permissions),
    }
    return ScenarioResult(
        name=name,
        passed=actual == expected,
        expected=format_mapping(expected),
        actual=format_mapping(actual),
    )


def invalid_credentials_scenario() -> ScenarioResult:
    response = login_keycloak("operator", "senha-incorreta")
    actual = {
        "credenciais_rejeitadas": response.status_code in {400, 401},
        "http_status": response.status_code,
    }
    expected = {
        "credenciais_rejeitadas": True,
        "http_status": "400 ou 401",
    }
    return ScenarioResult(
        name="Keycloak rejeita credenciais inválidas",
        passed=actual["credenciais_rejeitadas"],
        expected=format_mapping(expected),
        actual=format_mapping(actual),
    )


def run_scenario(name: str, scenario: Callable[[], ScenarioResult]) -> ScenarioResult:
    print_header(f"Cenário: {name}")
    try:
        result = scenario()
    except Exception as exc:
        result = ScenarioResult(
            name=name,
            passed=False,
            expected="execução sem erro",
            actual="erro durante a execução",
            error=str(exc),
        )

    print(f"Resultado esperado: {result.expected}")
    print(f"Resultado obtido:   {result.actual}")
    if result.error:
        print(f"Erro: {result.error}")
    print("Status: OK" if result.passed else "Status: FALHOU")
    return result


def main() -> int:
    print_header("Validação de políticas OPA e permissões Keycloak")
    print(f"OPA: {OPA_URL}/{OPA_POLICY_PATH}")
    print(f"Keycloak: {KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}")

    if not check_service_availability():
        return 1

    scenarios: list[tuple[str, Callable[[], ScenarioResult]]] = [
        (
            "OPA permite ação sem controle de bomba",
            lambda: opa_scenario(
                "OPA permite ação sem controle de bomba",
                "notifyUser",
                {"allowed": True, "approval_mode": "auto", "risk_level": "low"},
            ),
        ),
        (
            "OPA exige aprovação humana para ligar bomba",
            lambda: opa_scenario(
                "OPA exige aprovação humana para ligar bomba",
                "turnOnPump",
                {"allowed": False, "approval_mode": "human", "risk_level": "medium"},
            ),
        ),
        (
            "OPA exige aprovação humana para desligar bomba",
            lambda: opa_scenario(
                "OPA exige aprovação humana para desligar bomba",
                "turnOffPump",
                {"allowed": False, "approval_mode": "human", "risk_level": "medium"},
            ),
        ),
        (
            "Keycloak autentica admin/admin123",
            lambda: keycloak_user_scenario(
                "Keycloak autentica admin/admin123",
                "admin",
                "admin123",
                "pump_admin",
                {
                    ActionType.TURN_ON_PUMP.value,
                    ActionType.TURN_OFF_PUMP.value,
                    ActionType.NOTIFY_USER.value,
                },
            ),
        ),
        (
            "Keycloak autentica operator/operator123",
            lambda: keycloak_user_scenario(
                "Keycloak autentica operator/operator123",
                "operator",
                "operator123",
                "pump_operator",
                {
                    ActionType.TURN_ON_PUMP.value,
                    ActionType.TURN_OFF_PUMP.value,
                },
            ),
        ),
        (
            "Keycloak autentica viewer/viewer123",
            lambda: keycloak_user_scenario(
                "Keycloak autentica viewer/viewer123",
                "viewer",
                "viewer123",
                "viewer",
                {ActionType.NOTIFY_USER.value},
            ),
        ),
        (
            "Keycloak rejeita credenciais inválidas",
            invalid_credentials_scenario,
        ),
    ]

    results = [run_scenario(name, scenario) for name, scenario in scenarios]
    passed_count = sum(1 for result in results if result.passed)
    failed_count = len(results) - passed_count

    print_header("Resumo final")
    print(f"Total de cenários: {len(results)}")
    print(f"Aprovados: {passed_count}")
    print(f"Falhas: {failed_count}")

    if failed_count:
        print("\nCenários com falha:")
        for result in results:
            if not result.passed:
                print(f"- {result.name}")
        return 1

    print("\nTodos os cenários foram validados com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
