# HealthQueue Platform

A release platform for a patient appointment API, built as a platform engineering / SRE case study.

## Project Summary

HealthQueue is a Flask API for booking, viewing and cancelling patient appointments, wrapped in a delivery platform designed for auditable, low-risk releases. Every change is tested, scanned, signed, attested and deployed to Kubernetes by digest through a reviewed pull request, which Argo CD then reconciles into the cluster. Infrastructure is defined in OpenTofu, and reliability is tracked against SLOs computed from the API's own Prometheus metrics.

## Problem Statement

A health-tech startup needs to release its patient appointment API before launch, and every release must be auditable and low-risk. The team must be able to answer, for any version running in production, exactly which code it came from, what it contains, whether it was scanned, and who approved it. The platform makes Git the audit trail for every production change: an image reaches the cluster only through a merged pull request that names its signed digest. This project does not claim regulatory compliance; it builds the engineering controls such a claim would depend on.

## Technologies Used

| Area | Tools |
|------|-------|
| Application | Python 3.12, Flask, gunicorn, prometheus-client, pytest, pytest-cov |
| CI/CD and supply chain | GitHub Actions, Docker, GitHub Container Registry (GHCR), Trivy, Syft (SPDX SBOM), Sigstore Cosign (keyless), SLSA v1 provenance, Dependabot |
| Infrastructure | OpenTofu, AWS provider (VPC, S3), LocalStack Pro |
| Delivery | Argo CD, Kubernetes (local kind cluster), Kustomize |
| Observability | Prometheus (Kubernetes pod discovery, recording and alerting rules) |

## Architecture Overview

```text
commit (push to main)
  |
  v
CI (GitHub Actions, ci.yml)
  test (pytest, 85% coverage gate)
    -> build image
    -> Trivy scan (2 passes) + SBOM (Syft)
    -> sign / attest / verify (Cosign keyless), push to GHCR
    -> deploy PR (manifest updated to the image digest)
  |
  v
review + merge to main
  |
  v
Argo CD (polls main) --> Kubernetes, namespace healthqueue
                              ^
                              | scrapes /metrics every 15s
                         Prometheus, namespace monitoring

infra.yml: OpenTofu --> LocalStack (state bucket, VPC, S3 bucket)
```

**Test.** Every push and pull request runs the pytest suite with a coverage gate (`--cov-fail-under=85`). A failing test or a coverage drop stops the pipeline before an image is built.

**Build.** The image is built from a digest-pinned `python:3.12-slim` base, runs as a non-root user, and is passed to later jobs as a workflow artifact. It is not pushed anywhere until every check has passed.

**Trivy and SBOM.** In parallel, Trivy scans the image twice (see Key Design Decisions) and Syft generates an SPDX SBOM of everything inside it.

**Sign, attest, verify.** On `main` only, the push job pushes the image to GHCR, signs it with Cosign keyless signing, attaches the SBOM and a SLSA v1 provenance statement as attestations, and verifies the signature and both attestations against the expected workflow identity before continuing.

**Deploy PR.** The deploy job rewrites `manifests/deployment.yaml` to reference the new image by its digest and opens a pull request labelled `deploy`. Nothing changes in the cluster until a person merges it.

**Argo CD to Kubernetes.** Argo CD watches `main` and applies `manifests/` to the cluster. The pods run in a namespace that enforces the `restricted` Pod Security Standard, with a read-only root filesystem and all capabilities dropped.

**OpenTofu to LocalStack.** `infra.yml` checks formatting and runs `tofu plan` on pull requests, and `tofu apply` on `main` behind a `production` environment, all against an ephemeral LocalStack container. A separate bootstrap stack creates the bucket that holds the encrypted remote state.

**Prometheus.** A second Argo CD application deploys Prometheus into the `monitoring` namespace. It discovers annotated pods, scrapes the API every 15 seconds, keeps 30 days of data and evaluates the SLO rules.

## Key Design Decisions

| Decision | Why | Trade-off |
|----------|-----|-----------|
| **Digest-pinned deploy pull requests** | A digest identifies exactly one image, so the image that was scanned and signed is the image that runs. The PR makes each rollout a reviewed, revertible Git commit. | Each release needs a human merge, which adds a step and some lead time. |
| **Two-pass Trivy** | The first pass reports every HIGH/CRITICAL finding without failing; the second fails the build only on findings that have a fix. Unfixed base-image CVEs stay visible in the logs as accepted risk. | Known vulnerabilities without an upstream fix can ship. That is an explicit, logged risk decision, revisited when the base image is updated. |
| **Keyless signing limited to the push job** | Only the push job gets `id-token: write` and `packages: write`, and only on `main`. Test, build and scan jobs cannot sign or publish anything. | Signatures depend on Sigstore's public infrastructure, and pull requests produce no signed artifact to test against. |
| **Pinned dependencies** | Actions are pinned to commit SHAs, Python packages to exact versions, and base images to digests, so a re-pointed tag cannot change a build. Dependabot proposes weekly updates. | Updates become pull requests to review instead of arriving automatically. Transitive Python dependencies are not yet pinned. |
| **Encrypted, locked remote state with a bootstrap stack** | State is encrypted client-side (PBKDF2 + AES-GCM, enforced) before it reaches S3, and S3 native locking prevents concurrent writes. The bootstrap stack creates the state bucket and keeps it out of `infra/`'s own lifecycle. | The passphrase must be managed carefully: losing it means losing the state. The bootstrap stack itself uses local state. |
| **LocalStack instead of real AWS** | The full plan/apply workflow runs in CI with no cloud account, no cost and no risk to real resources. | LocalStack is an emulation; behaviour, IAM enforcement and limits can differ from AWS. |
| **Pull-based GitOps with prune and selfHeal** | The cluster pulls from Git, so CI never holds cluster credentials. `prune` removes resources deleted from Git and `selfHeal` reverts manual changes, so the cluster cannot drift from what was reviewed. | Emergency `kubectl` fixes are reverted; every change, even a hotfix, has to go through Git. |
| **One replica with in-memory storage** | Keeps the API simple while the platform around it is built. | Data is lost on restart and there is no redundancy. Scaling out requires an external database first. |
| **SLOs from the API's own Prometheus metrics** | The API exports request counts by route and status, and a latency histogram with a 250 ms bucket, so SLIs come straight from what users experience. | The SLIs only see requests that reach the app, and there is no data at all while it is down. |

## Challenges and How I Solved Them

**The first GHCR push was denied.** The container package's permissions did not let the repository's workflow write to it. I fixed the package permissions and granted `packages: write` only to the push job, rather than to the whole workflow.

**OpenTofu reported "decryption failed".** The passphrase used to read the state did not match the one it had been encrypted with. I re-encrypted the state with the current passphrase. CI reads it from the `TOFU_STATE_PASSPHRASE` secret, and local runs from `TF_VAR_state_passphrase`, so both must hold the same value.

**Pods were stuck in ImagePullBackOff.** The GHCR package was private, so the cluster could not pull it, and the manifest still pointed at a placeholder tag. I made the package pullable by the cluster and replaced the tag with the signed image digest, delivered through a deploy PR, which is now the only path an image takes into the cluster.

**Deploy PRs would have triggered rebuilds.** A merged deploy PR changes `manifests/deployment.yaml`, which would start the CI pipeline again and produce another image and another PR. `paths-ignore` in `ci.yml` now skips `manifests/`, `infra/`, `argocd/`, `observability/` and Markdown changes.

**Base-image CVEs with no fix.** Some HIGH/CRITICAL findings in Debian packages had no fixed version, so a single blocking scan failed every build. The two-pass Trivy design blocks only fixable findings while still reporting the rest.

**LocalStack loses state on restart.** LocalStack runs without persistence, so a restart deletes the state bucket and everything in it. Re-running the bootstrap stack recreates the bucket, and `tofu apply` in `infra/` rebuilds the resources from code.

## Outcomes and Metrics

- **Tests:** 42 pytest tests, 100% line coverage of `app/`, enforced by an 85% gate in CI.
- **Pipeline stages:** test, build, Trivy scan, SBOM, sign/attest/verify/push, deploy PR, plus a run summary in the GitHub Actions job summary.
- **Infrastructure:** 10 OpenTofu resources in `infra/`, applied to LocalStack with no drift.
- **Delivery:** Argo CD syncs a merged deploy PR to the cluster in about 3 minutes (its default polling interval).
- **SLOs:** 99.9% availability and 99% of requests under 250 ms, measured live in Prometheus over a 30-day window. An alert fires when 30-day availability drops below 99.9%, which means the error budget is spent and releases freeze.
- **Supply chain:** by my assessment, SLSA Build Level 1 is met (provenance exists and is published with the image). Levels 2 and 3 are partly met: provenance is signed and tied to the hosted builder, but it is generated inside the build job rather than by an isolated platform builder.

**DORA baseline: synthetic data.** To practise measuring delivery performance, I modelled a 90-day before/after baseline. These figures are synthetic, not measurements from a production system:

| Metric (synthetic) | Before | After |
|--------------------|--------|-------|
| Deployment frequency | 0.79 / day | 1.79 / day |
| Lead time for changes | 38.0 h | 10.9 h |
| Change failure rate | 21.2% | 10.5% |
| Time to restore service | 349 min | 22 min |

## What I Learned

- **CI/CD stages and gates:** ordering cheap checks first (tests, coverage) and gating expensive or irreversible steps (publishing, signing) behind them keeps feedback fast and failures contained.
- **Scanning in the pipeline:** a scanner that blocks on everything gets bypassed. Separating "report" from "block" made accepted risk explicit instead of hidden.
- **SBOMs and SLSA levels:** an SBOM says what is in an image; provenance says how it was built. Assessing against SLSA showed the gap between signed provenance and provenance from an isolated builder.
- **IaC remote state and locking:** state is both critical and sensitive. Remote storage, locking and client-side encryption each address a different failure, and the bootstrap stack resolves the "where does the state bucket's state live" problem.
- **GitOps reconciliation and drift:** with prune and selfHeal, Git is the desired state and the cluster converges to it, which turns drift from something to detect into something that is corrected automatically.
- **SLIs, SLOs and error budgets:** defining the SLI precisely (which routes, which status codes, which latency bucket) matters as much as the target, and an error budget turns reliability into a release decision.
- **DORA metrics and toil:** automating the deploy PR removed manual manifest edits, which is the kind of toil reduction DORA's lead-time and frequency metrics are meant to reflect.
- **The risks of AI-drafted pipelines:** AI-drafted configuration can be confidently wrong; during this project a drafted task referenced a pin list that did not exist. Every action SHA, image digest and version was therefore verified against its upstream source rather than trusted.

## Roadmap

In priority order:

1. **Platform-generated provenance (SLSA L2-3).** Provenance is currently generated and signed inside the build job ([ci.yml](.github/workflows/ci.yml)). Moving to an isolated, platform-generated provenance builder would close the gap to Level 3.
2. **Verify signatures before deploying.** CI verifies signatures and attestations, but the cluster does not. An admission policy that rejects unsigned images would enforce it at the point of deployment.
3. **Persistent storage outside the pod, then more replicas.** Appointments live in process memory ([app/store.py](app/store.py)), which forces one worker and one replica and loses data on restart.
4. **Staging-to-production promotion.** Argo CD deploys `main` straight to a single environment ([argocd/application.yaml](argocd/application.yaml)). A staging environment and a promotion step would let releases be verified before production.
5. **Lower the change failure rate.** To bring the (synthetic) 10.5% change failure rate down: broader automated testing, staging environments, feature flags to decouple deploy from release, and automated rollback driven by the SLO signals.

## Running It Locally

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/appointments` | Book an appointment (`patient_name`, `provider`, `scheduled_at` required; `reason` optional) |
| `GET` | `/appointments` | List appointments, optionally `?status=booked\|cancelled` |
| `GET` | `/appointments/<id>` | View one appointment |
| `DELETE` | `/appointments/<id>` | Cancel an appointment (kept with `status: cancelled`) |
| `GET` | `/health` | Liveness/readiness check |
| `GET` | `/metrics` | Prometheus metrics |

`scheduled_at` must be a future ISO 8601 datetime with a timezone offset, e.g. `2030-01-01T09:00:00Z`.
A provider cannot have two active appointments at the same time (`409`).

Storage is in memory, so data is lost on restart and the service runs as one gunicorn worker and one replica.

### Development

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # .venv/bin/pip on Linux/macOS
.venv/Scripts/pytest --cov=app --cov-report=term-missing
```

### Docker

```sh
docker build -t healthqueue-platform .
docker run -p 5000:5000 healthqueue-platform
```

### OpenTofu against LocalStack

Copy [.env.example](.env.example) to `.env`, fill in the passphrase and your LocalStack auth token, then:

```sh
set -a; . ./.env; set +a

docker run -d --name localstack -p 4566:4566 \
  -e LOCALSTACK_AUTH_TOKEN="$LOCALSTACK_AUTH_TOKEN" \
  localstack/localstack-pro:2026.9.0

# Create the state bucket first (run again after every LocalStack restart)
cd infra/bootstrap && tofu init && tofu apply

# Then the main stack, using the encrypted remote state
cd .. && tofu init && tofu plan && tofu apply
```

### Kubernetes, Argo CD and Prometheus

With Argo CD installed in the cluster, register both applications once; after that, Argo CD syncs them from `main`:

```sh
kubectl apply -f argocd/application.yaml -f argocd/observability.yaml
```

Open the Prometheus UI at http://localhost:9090 (Status > Targets shows the scraped API; Alerts shows the error-budget alert):

```sh
kubectl -n monitoring port-forward svc/prometheus 9090
```
