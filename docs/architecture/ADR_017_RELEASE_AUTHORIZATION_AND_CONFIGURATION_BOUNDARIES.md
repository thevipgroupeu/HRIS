# ADR 017: Shipped configuration and production authorization boundaries

Date: 2026-10-29. Status: implemented locally; deployment approval pending.

## Context

The readiness audit reproduced cross-company application status/withdrawal,
native Candidate detail and unowned media access. Local extension tests alone
also did not prove the Docker image included the app registrations: those lived
in `local_settings.py`, a deliberately excluded file. The operator intends Linux,
Docker Compose and Cloudflare Tunnel, with separate public/staff hostnames.

## Decision

- Ship TVG app registration and safe integration defaults in
  `horilla.settings.tvg`, imported explicitly by private settings. Preserve
  optional machine-local overrides but never depend on them for mandatory
  authorization or URLs. Import failures inside overrides are fatal. CareerHub
  continues to skip all private settings initialization.
- Preserve native Candidate/Employee semantics and use two small upstream hooks
  for recruitment-read scope and native-file authorization. Implement their
  policies in `tvg_core.access`. Candidate/CandidateDocument detail GETs must use
  the same policy as lists. Company-specific group grants are evaluated for each
  authorized company, not as an unrestricted union. Unknown media is denied.
- Keep public bridge tokens employer-scoped in addition to opaque person IDs.
  Lock withdrawal rows and preserve idempotence. IDs are not authorization.
- Keep PeopleHub registration/bootstrap/native administration routes absent.
  Its login requires an active linked employee; cookies remain host-only.
- Production rejects unsafe DEBUG/secrets/host configurations. Do not generate
  replacement secrets or run migrate/collectstatic during ordinary startup.
  A separately approved release procedure owns migrations/static publication.
- nginx has exact host routing, an unknown-host denial, ordinary untrusted HTTP
  and a dedicated trusted-connector listener. The same-host Tunnel overlay
  publishes only the trusted listener on loopback. Never expose it publicly or
  share the connector's network with untrusted workloads.
- Exclude all default private media stores, backup and credential directories
  from Git/build context. Runtime secrets remain outside images and Git.

## Consequences and limits

Missing TVG configuration can no longer silently produce an upstream-only image.
New strict settings may intentionally prevent an unsafe existing environment
from starting; operators must review secrets/hostnames before upgrading.

Native media support is an explicit allowlist: candidate CV/profile and
documents with matching permission/company, own employee photo, relevant policy
attachments. Other native file classes require a reviewed ownership rule before
rollout; do not disable the authorizer to restore broad access. TVG private legal
and verification files retain dedicated services. Full native HR write APIs are
not made Internet-safe by these read fixes and remain outside the portal URL tree.

No automatic deployment, real email, real accounts, agreement changes or data
deletion is authorized by this decision. See the hardening evidence and remaining
launch gates in `docs/hardening/2026-10-29/README.md`.
