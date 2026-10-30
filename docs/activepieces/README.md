# Activepieces lifecycle flows

The checked-in `initial-flows.json` is the secret-free deployment manifest for
the first Phase 15 flows. It is deliberately limited to the actions an
Activepieces administrator must reproduce/import in the target environment; it
contains environment-variable references, not credentials or webhook URLs.

## Shared trigger contract

All three flow specifications use one authenticated webhook trigger. The
checked-in manifest is intentionally provider/version-neutral rather than a
native Activepieces UI export: native exports pin piece and schema versions and
must be produced from the target Community Edition release after these specs
are imported/recreated. This prevents a repository upgrade from silently
deploying an obsolete piece version.

Configure the current Activepieces Webhook piece's HMAC mode with
`X-TVG-Body-Signature`, SHA-256, hex encoding and the `v1=` prefix. The secret
belongs in the target environment's Activepieces secret store; replace the
manifest reference there and never commit it. Before using any message field,
the flow must:

1. let the native trigger verify HMAC-SHA256 over the exact request body against
   `X-TVG-Body-Signature`;
2. optionally verify the additional timestamp-bound `X-TVG-Signature` over
   `<X-TVG-Timestamp>.<exact request body>` when the target release supports a
   custom verifier;
3. reject `X-TVG-Timestamp` values older/newer than five minutes;
4. persist `Idempotency-Key` in the Activepieces idempotency store and return the
   prior result for a duplicate;
5. require `schema == eu.thevipgroup.lifecycle-event` and
   `schema_version == 1`;
6. route only the event type declared by the flow.

The email action uses the approved, already-rendered `message` object. It must
not replace `from`, `reply_to`, subject or text. The Zoho connection is an
environment-owned Activepieces connection and is never exported. Recruitment
flows must reject a sender other than `recruitment@thevipgroup.eu`.

After acceptance and after every terminal result, post the allow-listed status
to `/internal/automation/v1/executions/`. Sign the exact callback body with the
separate `TVG_CALLBACK_HMAC_SECRET`; include `X-TVG-Timestamp`,
`X-TVG-Signature` and a unique callback `Idempotency-Key`. The callback supports
only execution history and cannot modify an applicant, employee or document.

## Deployment sequence

1. Import/recreate the three manifest flows in staging.
2. Create separate Activepieces secrets for outbound verification, callback
   signing and Zoho. Never paste them into this repository.
3. Configure and approve one company message-template version for each
   `workflow_key` in Horilla.
4. Set the staging Horilla webhook URL and independent HMAC secrets, then enable
   automation. Staging must use approved synthetic recipients or a local test
   mail service until the deployment owner authorizes external email.
5. Submit one synthetic application, shortlist it, and queue one synthetic
   document reminder. Confirm one execution and one email-sink record for each.
6. Test a duplicate event, bad signature, transient failure, cancellation and
   dead-letter replay before production enablement.

The remaining Phase 15 workflows are catalogued in `tvg_automation/events.py`
with their Horilla-side human-approval boundary. They are intentionally not
enabled by this initial manifest.

## Low-touch PeopleOps operation

The scheduler checks the durable outbox every minute. Successful events need no
operator action. The normal operating queue is therefore limited to two
Horilla admin filters:

- `Awaiting human approval` for actions that cannot legally or safely be
  automated; and
- `Failed / review required` for exhausted or non-retryable deliveries.

An authorized HR operator approves message-template versions once, then works
only these exception queues. Transient failures retry automatically with the
same idempotency key. A failed event can be replayed only with a permission and
an audit reason, and no more than five times. Cancellation is available before
delivery but not while a worker owns the delivery lease.

This makes routine recruitment communication manageable by a small HR team,
potentially one day-to-day operator where company policy permits. It does not
collapse required separation of duties: hiring decisions, legal signatures,
acceptance issuance, asset/account deprovisioning, and security approvals stay
with their authorized humans.

For a co-located Activepieces deployment, use the private callback address
`http://web:8000/internal/automation/v1/executions/`. For a separate staging
deployment, expose only that exact signed callback route through the controlled
integration ingress; never expose the whole PeopleOps service or give
Activepieces database credentials.
