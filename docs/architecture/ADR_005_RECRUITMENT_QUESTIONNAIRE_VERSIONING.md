# ADR-005: Recruitment Questionnaire Versioning and Submission Evidence

## Status

Accepted for the configurable-questionnaire increment on 2026-10-26.

## Context

Horilla already owns questionnaire authoring through `SurveyTemplate`,
`RecruitmentSurvey`, `Recruitment.survey_templates` and
`RecruitmentSurveyAnswer`. Those models provide company-scoped templates,
required/optional questions and the common input types CareerHub needs. The
configuration is mutable, however, and native answers are keyed by mutable
question text. That is not enough to prove which form an applicant submitted.

CareerHub and PeopleOps remain separate runtimes and databases. The public
runtime must render only safe questionnaire fields, reject stale or tampered
forms, preserve application-time evidence, and never expose native database
identifiers.

## Decision

- Horilla's native survey models remain the questionnaire authoring system of
  record. HR assigns one or more existing survey templates through the
  `JobOpening` admin form; the assignment is persisted on the associated
  Horilla `Recruitment`.
- `RecruitmentQuestionnaireVersion` is an additive, immutable extension. It
  stores a sequential version, a SHA-256 fingerprint of the effective native
  definition, and a definition snapshot. A native question keeps the same
  random public question UUID across later versions.
- CareerHub's public job contract includes only the public version UUID,
  version number, fingerprint, question UUID, label, type, required flag and
  choice values. Native question/template ids and all HR-only data remain
  private.
- Publication and admin updates materialize the current version. Public reads
  are read-only and fail closed when the current native configuration has no
  matching stored version or is invalid. Submission must present the exact
  current version tuple, so an applicant is told to reload after HR changes a
  form.
- CareerHub validates the dynamic form and freezes its version and complete
  answer snapshot with the local application evidence. PeopleOps validates it
  again against the stored version before creating the existing Horilla
  `Candidate`, `RecruitmentSurveyAnswer` and `tvg_careers.Application` link.
  Unanswered optional questions remain in the immutable snapshot with a null
  answer.
- Generic native `file` questions are deliberately not public. The initial
  application accepts only the dedicated CV and cover-letter fields,
  preventing a questionnaire author from requesting identity documents at
  this stage.
- CV and cover-letter uploads accept PDF and DOCX up to 5 MB. Validation checks
  filename extension, declared MIME type and content structure; PDFs with
  known active markers and DOCX archives with macros, embedded/OLE/ActiveX
  content, encrypted entries, traversal paths or excessive expansion are
  rejected.
- The shared Horilla `Candidate.resume` validator is minimally extended to
  accept the same validated DOCX format because that upstream field remains
  the authoritative submitted CV. The historical validator function name is
  retained for migration compatibility; no Candidate schema or semantics are
  replaced.
- Application source is an explicit enum: company website, LinkedIn,
  ErasmusIntern, job board, university referral or referral. Only
  `utm_source`, `utm_medium`, `utm_campaign`, `utm_term` and `utm_content` are
  retained, with bounded normalized values. Unknown source values are rejected
  at the private command boundary and unknown campaign keys are discarded.
- Successful creation writes one idempotent, data-minimized
  `career.application_submitted.v1` confirmation event in the same database
  transaction. This is an outbox record; external delivery and retry
  processing belong to `tvg_automation` and are not performed in the web
  request.

No questionnaire response, including Erasmus+ declarations, automatically
accepts or rejects an applicant. Nationality and other protected
characteristics are not eligibility rules.

## Authorization and tenancy

- Assigning templates requires the existing Recruitment change permission;
  publishing/closing retains the dedicated JobOpening permissions.
- Templates must belong to the opening's legal employer or be an explicitly
  general, company-null Horilla template. Questions shared with a foreign
  company's template are also rejected because they would be mutable across
  tenants. Invalid cross-company configuration fails validation and cannot be
  published through CareerHub.
- Public catalogue and submission queries fail closed to the configured single
  `TVG_LEGAL_EMPLOYER_ID`. Application commands still require the dedicated
  service credential and stable Person/email pair.
- Candidate drafts, files and snapshots remain owner-scoped in CareerHub.

## Consequences

- HR keeps Horilla's existing questionnaire UI and answer records; the fork
  adds provenance without a replacement survey model.
- Editing a native question produces a new immutable version. Existing
  applications retain their previous definition and answers.
- A form changed between page load and submission is rejected rather than
  silently interpreted against a new schema.
- Structural file checks are defence in depth, not malware scanning.
  Production still requires quarantine/scanning and approved
  retention/erasure handling.
- Confirmation delivery is intentionally pending the durable automation
  worker. The committed outbox event is safe to retry and contains opaque ids,
  venture and source but no applicant email or document content.

## Verification

Automated coverage includes native-field projection, required/optional
validation, different venture questionnaires, immutable version creation,
stale-version rejection, cross-company denial, source/UTM allow-listing,
PDF/DOCX validation and size limits, active-content rejection, idempotent event
creation, one Person applying to multiple openings, and CareerHub owner
isolation.
