# Linux local-server rollout plan

Confirmed target: Linux, Docker Compose and Cloudflare Tunnel. This is a reviewed
preparation plan, **not permission to deploy**. The existing live repository must
be reconciled with this checkout first; see `SERVER_HANDOFF.md`.

## Intended topology

Cloudflared runs as a restricted host service and connects to nginx at
`127.0.0.1:8080`. The application stack uses the base, production and tunnel
Compose files together. There are no inbound router port-forwarding requirements
for the app; do not expose PostgreSQL, Redis, Gunicorn or port 8080 on the LAN or
Internet. Secure SSH/admin access separately. Enable Cloudflare HTTPS redirects
and staff Access/MFA policy; allow external candidates to reach CareerHub.

The tunnel overlay assumes a host service, not a cloudflared container. If this
changes, review the connector's isolated network and trusted-header policy first.

## Operator preparation

- Use a supported, patched Linux/Docker installation and sufficient storage,
  memory and CPU measured by staging load. Record actual server resources and
  expected workload; the local SQLite timings are not a sizing benchmark.
- Run the app under non-root container users as configured. Keep the Docker
  socket and membership in the docker group restricted to trusted operators.
- Maintain off-site encrypted backups; a second directory on this server is not
  disaster recovery. Monitor disk, certificate/tunnel availability, scheduler,
  queue failures, scan failures and backup age. Assign backup operator coverage.
- Put production secrets in an operator-managed file/secret system with limited
  OS permissions, not this repository. Use independent CareerHub/private secrets
  and databases. Retain adequate existing secrets; plan rotation explicitly.
- Set private `ALLOWED_HOSTS` to include `web` for the internal bridge and only
  approved private hostnames. CareerHub and PeopleHub each enforce their own
  exact hostname. Do not use `*` or a shared parent-domain cookie.
- Keep external automation disabled until individually accepted. For staging,
  use a real test mail sink, synthetic people/files and no real recipients.

## Release sequence after the remaining gates and explicit approval

1. Build and scan an immutable image from the reviewed commit. Verify the image
   contains `horilla/settings/tvg.py`, all TVG apps, migrations and templates;
   `local_settings.py`, operator `.env` and private media must be absent.
2. On isolated staging, render the three-file Compose config without printing
   secrets. Run `scripts/check_tvg_compose.py` to check the synthetic configuration.
   Test PostgreSQL migrations and real Redis concurrency under Python 3.12.
3. Prepare database/cache services, then run two explicit migration jobs using
   the correct private and public settings. Ordinary production startup now
   skips migrations and collectstatic; restarting an app cannot run DDL or clear
   static files. Do not run migration jobs against live DBs without the approved
   backup checkpoint and change window.
4. Collect static assets explicitly as a release task while traffic is stopped,
   or into a versioned release store. Do not clear the shared static volume while
   active workers serve from it. Retain the previous static/image pair.
5. Start the application services and one scheduler; check strict container
   health responses. Health checks do not establish DB/mail/scanner readiness.
6. Test the full synthetic two-application lifecycle through the actual staging
   proxy and mail sink. Rehearse backup/restore and rollback on a clean target.
7. Only after release sign-off, update the server repository/image and tunnel
   configuration in the approved change window. Keep rollback checkpoints and
   verify no real notification or account task is accidentally replayed.

## Go/no-go

Do not invite real candidates yet. A real scanner/quarantine integration,
complete paired backup/restore, end-to-end staging, reviewed privacy/templates,
full native-HR access plan and final image/security checks remain mandatory.
Consult the original audit checklist for all gates; local login/proxy test success
is only part of production readiness.
