# Third-Party Notices

This repository's own source code is licensed under the MIT License. See
`LICENSE`.

The Docker Compose demonstration also pulls and runs third-party container
images and installs third-party Python packages. Those components are not
relicensed by this project and remain subject to their upstream licenses.

## Docker Compose Runtime Components

| Component | Compose service or Dockerfile use | License noted by upstream | Notes |
| --- | --- | --- | --- |
| MongoDB Server (`mongo:7.0`) | `mongo` service | SSPL-1.0 | Used as the local database for the demonstration. Do not present this Compose stack as entirely OSI-approved open source. |
| FIWARE Orion Context Broker (`fiware/orion:3.10.1`) | `orion` service | AGPL-3.0-only | Used unmodified as a separate service for local NGSI-v2 context management. |
| Open Policy Agent (`openpolicyagent/opa:0.68.0`) | `opa` service | Apache-2.0 | Runs the local Rego policy supplied in `policies/`. |
| Keycloak (`quay.io/keycloak/keycloak:26.0`) | `keycloak` service | Apache-2.0 | Runs unmodified as the IAM service; the `smartcity` realm in `keycloak/realm-export.json` is imported on startup. |
| Python Docker Official Image (`python:3.11-slim`) | Base image for project services | PSF License plus licenses of included OS packages | Used as the base image for the locally built application services. |
| uv (`ghcr.io/astral-sh/uv:latest`) | Copied into project service images | Apache-2.0 OR MIT | Used to install and run Python dependencies in the locally built images. |
| Python packages from `pyproject.toml` and `uv.lock` | Installed inside project service images | Package-specific licenses | These packages keep their own licenses and notices. |

## Research Demonstration Scope

This Compose setup is intended for local reproduction of the scientific
demonstration and is not a managed public database, context broker, policy
engine, or cloud service offering.

The project does not modify MongoDB Server, FIWARE Orion, OPA, Keycloak, Python,
uv, or the listed Python dependencies. If built container images are redistributed,
publish the corresponding third-party notices and preserve upstream license
information for the included base images, binaries, and packages.

## Suggested Paper Statement

The prototype code developed for this work is released under the MIT License.
The Docker Compose environment used for local reproduction includes third-party
components under their own licenses, including MongoDB Server under SSPL-1.0,
FIWARE Orion Context Broker under AGPL-3.0, and Open Policy Agent under
Apache-2.0. These components are used unmodified as separate services in a
research demonstration environment and are not relicensed by this project.

## Upstream References

- MongoDB licensing: https://www.mongodb.com/community/licensing
- FIWARE Orion Context Broker: https://github.com/telefonicaid/fiware-orion
- Open Policy Agent: https://github.com/open-policy-agent/opa
- Keycloak: https://github.com/keycloak/keycloak
- Python Docker Official Image: https://hub.docker.com/_/python
- Python licensing: https://www.python.org/psf/summary/
- uv license: https://github.com/astral-sh/uv
