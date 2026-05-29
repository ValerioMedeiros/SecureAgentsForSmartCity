# Testing IAM (Keycloak)

Reproducible runbook to validate the **Identity and Access Management (IAM)**
integration with Keycloak — the Generic Enabler that authenticates operators and
enforces the authorization boundary between plan generation and execution.

What this document covers:

1. Start the stack (infra + services + Keycloak).
2. Confirm the `smartcity` realm was provisioned automatically.
3. Access the Citizen-Facing Interface and log in.
4. Validate role-based access control (RBAC) in the approval flow.
5. Validate authentication and tokens from the command line (optional).
6. Check traceability: who approved each action in the audit log.
7. Inspect the realm in the Keycloak admin console.
8. Run the scenario in open mode (`KEYCLOAK_ENABLED=false`).

Prerequisites: `docker`, `docker compose`, `curl`, `python3`. `jq` is optional.

---

## 0) Overview of what gets provisioned

On startup, Keycloak imports `keycloak/realm-export.json`
(`start-dev --import-realm`) and creates:

- realm **`smartcity`**;
- confidential client **`smartcity-poc`** (direct access grant enabled);
- realm roles **`pump_admin`**, **`pump_operator`**, **`viewer`**;
- three test users:

| User       | Password      | Role            | Allowed actions                       |
| ---------- | ------------- | --------------- | ------------------------------------- |
| `admin`    | `admin123`    | `pump_admin`    | `turnOnPump`, `turnOffPump`, `notifyUser` |
| `operator` | `operator123` | `pump_operator` | `turnOnPump`, `turnOffPump`           |
| `viewer`   | `viewer123`   | `viewer`        | `notifyUser`                          |

Relevant addresses:

- Citizen-Facing Interface (operator panel): <http://localhost:8020>
- Keycloak admin console: <http://localhost:8090> (admin/admin)
- Realm OIDC discovery: <http://localhost:8090/realms/smartcity>

---

## 1) Start the stack

```bash
docker compose up -d --build
```

> Optional: to test IAM only, you can bring up just Keycloak and the
> Citizen-Facing Interface:
>
> ```bash
> docker compose up -d --build keycloak citizen-interface
> ```

Keycloak takes ~20–40s on first start to import the realm. Wait until the realm
endpoint responds:

```bash
# repeat until you get HTTP 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8090/realms/smartcity
```

---

## 2) Confirm the realm provisioning

```bash
curl -s http://localhost:8090/realms/smartcity \
  | python3 -c "import sys,json; d=json.load(sys.stdin); \
print('realm:', d['realm'], '| public_key:', bool(d.get('public_key')))"
```

Expected output:

```
realm: smartcity | public_key: True
```

---

## 3) Access the panel and log in

1. Open <http://localhost:8020/login> in the browser.
2. Log in as **operator** / `operator123`.
3. The top bar should show the authenticated user and roles:
   `🔐 operator (pump_operator) sair`.

> If `KEYCLOAK_ENABLED=false`, the bar shows `🔓 IAM desativado (modo PoC
> aberto)` and login is not required (see section 8).

---

## 4) Validate RBAC in the approval flow

The goal is to prove that **only an authenticated operator with the right role**
can approve a pump actuation. We create an approval request (normally issued by
the Weather Agent via `request_human_approval`) and try to decide it under three
conditions.

### 4.1 Create a pending approval request

```bash
AID=$(curl -s -X POST http://localhost:8020/approvals \
  -H 'Content-Type: application/json' \
  -d '{"pump_id":"PumpDevice:001","action":"turnOnPump","risk_level":"medium","reason":"heavy rain"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['approval_id'])")
echo "approval_id=$AID"
```

The approval also shows up in the panel at <http://localhost:8020>.

### 4.2 Decide WITHOUT authentication → redirected to /login

```bash
curl -s -o /dev/null -w "HTTP %{http_code} -> %{redirect_url}\n" \
  -X POST http://localhost:8020/approvals/$AID/decide -d 'decision=s'
```

Expected: `HTTP 303 -> http://localhost:8020/login?error=...` (access denied,
forwarded to login).

### 4.3 Log in as `viewer` (no pump permission) → 403 Forbidden

```bash
# viewer login (stores the session cookie)
curl -s -c /tmp/viewer.jar -o /dev/null \
  -X POST http://localhost:8020/login -d 'username=viewer&password=viewer123'

# try to approve a turnOnPump
curl -s -b /tmp/viewer.jar -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST http://localhost:8020/approvals/$AID/decide -d 'decision=s'
```

Expected: `HTTP 403` — the `viewer` role does not authorize `turnOnPump`.

### 4.4 Log in as `operator` (has `pump_operator`) → approval completed

```bash
curl -s -c /tmp/op.jar -o /dev/null \
  -X POST http://localhost:8020/login -d 'username=operator&password=operator123'

curl -s -b /tmp/op.jar -o /dev/null -w "HTTP %{http_code} -> %{redirect_url}\n" \
  -X POST http://localhost:8020/approvals/$AID/decide -d 'decision=s'
```

Expected: `HTTP 303 -> http://localhost:8020/` (approval recorded).

> In the browser you observe the same: logged in as `viewer`, clicking
> **Aprovar** returns 403; logged in as `operator` or `admin`, the approval is
> accepted and disappears from the pending list.

---

## 5) Authentication and tokens via CLI (optional)

### 5.1 Obtain an access token

```bash
./keycloak/get_token.sh operator operator123
```

Prints an RS256 JWT signed by the realm. Swap for `viewer viewer123` or
`admin admin123` to get other roles.

### 5.2 Inspect the roles inside the token

```bash
TOKEN=$(./keycloak/get_token.sh operator operator123)
echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); \
print('user:', d['preferred_username'], '| roles:', d['realm_access']['roles'])"
```

### 5.3 Invalid credentials are rejected

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST http://localhost:8090/realms/smartcity/protocol/openid-connect/token \
  -d grant_type=password -d client_id=smartcity-poc \
  -d client_secret=smartcity-poc-secret \
  -d username=operator -d password=wrong-password
```

Expected: `HTTP 401`.

---

## 6) Traceability: who approved?

Every approval decision records the authenticated operator as the `actor` in the
hash-chained audit log. Query it through the monitor endpoints:

```bash
# approval events recorded by the citizen-interface
curl -s "http://localhost:8010/audit/entries?component=citizen_interface" \
  | python3 -c "import sys,json; \
[print(e['event_type'],'actor=',e['actor'],'outcome=',e['outcome']) \
 for e in json.load(sys.stdin)['entries'] if 'APPROVAL' in e['event_type'] or e['event_type']=='OPERATOR_LOGIN']"
```

Expected output (reflecting section 4):

```
OPERATOR_LOGIN actor= viewer outcome= authenticated
APPROVAL_FORBIDDEN actor= viewer outcome= forbidden
OPERATOR_LOGIN actor= operator outcome= authenticated
APPROVAL_DECIDED actor= operator outcome= approved
```

This closes the accountability loop: plan (generation), policy verdict and human
decision stay correlated — and the approval is attributable to a verified
identity.

---

## 7) Keycloak admin console

1. Open <http://localhost:8090> and log in with **admin / admin**.
2. In the realm selector (top left), switch to **smartcity**.
3. Under **Clients → smartcity-poc**, confirm *Direct access grants* is enabled.
4. Under **Realm roles**, confirm `pump_admin`, `pump_operator`, `viewer`.
5. Under **Users**, confirm `admin`, `operator`, `viewer` and their *Role
   mapping*.

---

## 8) Open mode (no IAM)

To reproduce the original PoC behavior, without authentication:

1. Set `KEYCLOAK_ENABLED=false` on the `citizen-interface` service
   (in `docker-compose.yml` or via `.env`) and recreate the container:

   ```bash
   docker compose up -d --force-recreate citizen-interface
   ```

2. In this mode the `/approvals/{id}/decide` endpoint does not require login and
   any decision is accepted (the audit `actor` becomes `human_operator`). The
   static fallback tokens then apply in `SecurityManager`:
   `admin → token123`, `operator → token456`, `viewer → token789`.

---

## 9) Troubleshooting

- **`/login` returns "Keycloak unreachable"**: Keycloak is still starting or
  `KEYCLOAK_URL` is wrong. Inside the compose network it must be
  `http://keycloak:8080`; from the host, `http://localhost:8090`.
- **Login OK but "Falha ao validar token"**: the service could not fetch the
  realm JWKS. Check connectivity to `KEYCLOAK_URL` and that the `smartcity`
  realm is up.
- **Realm not imported**: confirm that `keycloak/realm-export.json` is mounted at
  `/opt/keycloak/data/import/` (see the `keycloak` service volume) and that the
  command includes `--import-realm`. Check the logs:
  `docker compose logs keycloak | grep -i import`.
- **Unexpected 403 when approving**: the logged-in user lacks the role required
  for the requested action (e.g. `viewer` attempting `turnOnPump`). Use
  `operator` or `admin`.

---

## Acceptance criteria summary

| Check                                                    | Expected            |
| -------------------------------------------------------- | ------------------- |
| Realm `smartcity` provisioned on startup                 | HTTP 200 on realm   |
| Decide approval without login                            | 303 → `/login`      |
| `viewer` approving `turnOnPump`                          | 403 Forbidden       |
| `operator`/`admin` approving `turnOnPump`                | 303 → `/` (approved)|
| Invalid password at the token endpoint                   | 401                 |
| Audit records `actor=<user>` on the decision             | yes                 |
| `KEYCLOAK_ENABLED=false` restores open mode              | no login required   |
