# ADR-009: Erasmus+ documentation lifecycle

## Status

Accepted for Phase 10 implementation on 2026-10-27.

## Context

Phase 9 ends when the selected candidate confirms receipt of an authorized
acceptance letter. Learning Agreements and subsequent university/company
documents contain candidate information and legal attestations, may change
during mobility, and can require several independent signatures. They must not
be stored in public Horilla media, overwritten in place, or treated as signed
because a template was copied or an external request was created.

University requirements vary. NA10 requirements and protocols have not been
approved, and a self-hosted signing product has not yet been selected.

## Decision

Keep the workflow in the isolated `tvg_erasmus` app and link every document to
the existing `ErasmusCase`. Approved `ErasmusDocumentRequirement` rows define a
legal employer, optional university/programme scope, lifecycle phase,
required/optional flag, signer roles and optional external-system key. Approved
requirement versions are immutable and each case receives an exact snapshot. A
more-specific approved configuration replaces a wildcard with the same code;
existing case documents never change when configuration changes.

Only a received Phase 9 acceptance can open documentation. Initially, only
approved Before Mobility Learning Agreement requirements are materialized.
Once an authorized human marks the fully signed Learning Agreement returned,
the remaining matching company and later-phase requirements are materialized.
During-mobility amendments are new documents linked to the signed agreement;
they do not overwrite it.

Files are validated PDF/DOCX uploads in a dedicated private storage root. Each
upload or approved-template copy creates an immutable, digest-bearing version.
Company legal-document requirements must reference a human-approved template
of the same document type. Copying a template only creates a draft version; it
does not merge invented fields, create a signature or produce a final legal
document.

Transitions are requested, submitted, reviewed, revision needed, approved,
signed and returned. Human permissions gate review, approval, signature
attestation and return. Signatures identify a required role and timestamp and
are bound to the latest immutable version. Manual signed upload is the valid
fallback. The signing adapter is disabled by default, idempotent at the request
boundary and cannot bypass approval.

CareerHub can upload and download only documents belonging to its authenticated
Person through the existing internal service boundary. Staff access additionally
requires company, recruitment relationship and action permission checks.
Downloads are streamed with no-store/nosniff/sandbox headers and create audit
events.

After a Learning Agreement is signed, CareerHub asks: “Do you need assistance
finding accommodation in Kraków?” The record contains only the boolean request,
time and follow-up status/actor; it has no free-text personal details.

NA10 is represented only by an optional configuration key and a disabled
provider interface. No NA10 schema, validation rule or outbound request is
implemented until an approved specification exists.

## Consequences

- Before Mobility completion is derived only after all required Before Mobility
  documents satisfy their configured signature/return requirements.
- Templates, document versions, signatures and audit events retain history and
  cannot be edited in place.
- Private document retention/erasure and production malware scanning remain
  release prerequisites; structural PDF/DOCX validation is defense in depth,
  not antivirus scanning.
- Real signing and NA10 integrations require separately reviewed adapters,
  credentials, webhook authentication, retry/idempotency and operational
  ownership before configuration is enabled.
