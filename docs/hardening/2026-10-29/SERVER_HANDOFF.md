# Server repository handoff — review only, no automatic deployment

## Coordinate the two repositories first

The live server uses another repository. Ask its maintainer to identify its
commit, runtime settings, nginx virtual hosts, tunnel ingress, database names and
volume inventory **without sharing credentials or candidate data**. Compare the
changes with this checkout; do not overwrite their independent work. The large
pre-existing TVG working tree must be reviewed and committed as a release artifact.
Record the current image digests/config version for rollback.

## Required runtime mapping

| Origin host | WSGI/settings | Allowed scope |
| --- | --- | --- |
| `career.thevipgroup.eu` | `horilla.careerhub_wsgi` / `horilla.settings.careerhub` | Public catalog and isolated candidate authentication |
| `people.thevipgroup.eu` | `horilla.peoplehub_wsgi` / `horilla.settings.peoplehub` | Employee-only portal, no self-registration |
| Private service network only | existing `web` / private PeopleOps settings | Authenticated CareerHub bridge; restricted HR operations |

CareerHub needs its own database and Redis credentials, separate secret/cookie
names and SMTP credentials. PeopleHub must never use the public candidate user
model. Accounts for employees are provisioned only through authorized HR/IT
workflows. Cloudflare Access is an additional gate, not a substitute for Django
authorization; CareerHub registration must remain usable by external candidates.

## Optional same-host Tunnel topology

Review `docker-compose.tunnel.yml` and `docker/cloudflared.ingress.example.yml`.
They assume cloudflared runs on the same trusted host, **outside Docker**:

```text
Cloudflare HTTPS -> host cloudflared -> 127.0.0.1:8080 -> nginx
  career.thevipgroup.eu -> careerhub:8000
  people.thevipgroup.eu -> peoplehub:8000
```

The optional overlay replaces public port 80 publication with loopback-only
8080. On that listener nginx trusts the connector's exact HTTPS header and
Cloudflare client-IP header, replacing rather than appending caller-supplied
X-Forwarded-For. CareerHub must therefore use
`CAREERHUB_TRUSTED_PROXY_COUNT=1`. Never publish this listener to the Internet;
isolate the Docker network from untrusted workloads. An operator running
cloudflared in Docker must design/validate that separate network topology instead
of copying the loopback example. Plain port 80 does not trust these headers.

Enable HTTPS at the edge (HTTP redirect), preserve the correct Host, and retain
the catch-all 404. Review Cloudflare Access on the staff hostname. Validate
ingress with `cloudflared tunnel ingress validate` and `cloudflared tunnel ingress
rule` against the operator's approved configuration. No tunnel credentials belong
in this repository. The app's `SECURE_SSL_REDIRECT` remains enabled on public and
staff runtimes; a health check on direct HTTP may require the trusted internal
scheme/header or a deliberately restricted health exception.

The production Compose overlay now supplies that exact Host/HTTPS header from
inside each container and checks the expected health body and absence of a
redirect. These are process liveness checks, not proof the DB, mail or scanner
is ready. Private `ALLOWED_HOSTS` must also include `web` for the bridge client.
The example timezone is now Europe/Warsaw for new installations; review DST and
existing scheduled deadlines before changing any deployed timezone.

## Stage before changing the live server

1. Use separate staging PostgreSQL 16/Redis 7 databases, temporary media and a
   test mail sink. Do not reuse live `.env`, candidate uploads or accounts.
2. Review the actual diff and final image scan. Run the release scripts and CI.
3. Take paired backups of both production databases and all private file stores
   only under the operator's approved procedure; verify restore/key availability.
4. Rehearse migrations independently for each settings registry. Public `0006`
   is an event-choice metadata migration, not a data rewrite. Do not delete prior
   events when rolling it back. Public forward/backward/forward passed SQLite;
   PostgreSQL rehearsal is still required. Reuse the audited migration/runbook
   sequence for the complete graph and prohibit uncontrolled startup migrations.
5. Supply strong existing secrets if already adequate. If a weak key must change,
   plan session/token invalidation and user communication; never silently copy
   the public key into private settings. The new guards intentionally prevent
   unsafe production processes from starting.
6. Verify both routes through staging nginx, then through Cloudflare: candidate
   signup/sign-in must return forms; internal login must work for an approved
   synthetic employee. All internal signup/legacy creation routes must 404 and
   leave the user count unchanged, for anonymous and authenticated callers.
7. Confirm email verification/reset through the fake mail sink, session rotation,
   disabled-user logout, first-password change, host-only cookies, CSRF and no
   redirect loop. Never log verification URLs, tokens or personal documents.
8. Complete the audit's remaining functional/security/operations gates. Obtain
   separate release approval before any production cutover.

## Rollback checkpoints

- Keep the previous immutable image/config and tested paired backup checkpoint.
- Do not relax authorization, re-enable DEBUG, expose native HR URLs or turn off
  scanning to make a failed deployment appear healthy.
- Prefer compatible application/config rollback without rolling back data. If
  migration rollback is required, rehearse it and explicitly reconcile writes
  since cutover; never restore only the database while leaving mismatched files.
- A candidate nginx 404 after cutover warrants checking the matching ingress rule,
  nginx `server_name`, upstream service/image and public ROOT_URLCONF. Do not reset
  accounts or databases: a missing route is not a bad password.
- No production command, migration, email or account change was executed in this
  hardening pass. The disposable nginx test container had no volumes/network and
  was removed after verification; no user data was removed.
