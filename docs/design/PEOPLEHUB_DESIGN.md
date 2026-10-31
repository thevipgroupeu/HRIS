# THEVIPGROUP PeopleHub design and product direction

## Purpose

PeopleHub is the private workplace for employees and interns. It is not a
replacement for the platform's employee, leave, attendance, asset, performance,
onboarding, or request modules. It presents approved, employee-scoped views of
those records through one consistent THEVIPGROUP experience.

The primary user question is: **What do I need to know or do next?** The
dashboard therefore prioritizes deadlines and required actions before historical
records and reference information.

## Brand evidence

The design was checked against the public THEVIPGROUP website on 31 October
2026. The inspected site metadata and styles use:

- Poppins as the primary typeface;
- `#14338C` as the primary blue;
- `#07112C` as the dark brand color;
- white backgrounds with muted blue-grey supporting text;
- strong editorial headings, dark calls to action, and restrained card layouts.

PeopleHub adapts that language for a denser authenticated application. Brand
colors identify hierarchy and state, but native status colors remain semantic.
The supplied THEVIPGROUP logo is used in the application shell and login view.

The public-site screenshot asset could not be opened by the local visual
inspection runtime, so this record relies on the site's retrieved brand fields,
HTML, and stylesheet evidence rather than an inferred pixel-perfect copy.

## Information architecture

### Employee and intern home

1. Identity, venture, department, position, supervisor, and work model.
2. Personalized next actions across reporting, training, projects, and onboarding.
3. Leave balances, requests, attendance, and assigned equipment.
4. Training, objectives, feedback, weekly reports, and presentations.
5. Current announcements, relevant holidays, and assigned policies.
6. Private support requests for HR, IT, equipment, documents, and accommodation.

### Supervisor workspace

The same personal workspace plus direct-report access, overdue mandatory
training alerts, report review, and team requests. Team access remains limited to
direct reports unless an explicit HR or management permission grants broader
company access.

### HR, IT, and management

Role-aware navigation exposes only the queues permitted by the existing Django
permissions. Object-level company checks continue to apply after a menu item is
visible. Navigation is never treated as authorization.

## Privacy and security rules

- Shared directory fields are allowlisted: name, company email, position,
  department, venture, manager, and work location.
- Personal address, phone, personal email, identity, medical, payroll, and
  immigration information must not appear in directory or team summaries.
- Announcements, holidays, policies, requests, and team members are explicitly
  company- and audience-scoped.
- Announcement descriptions are rendered as plain text in summaries.
- Documents must use controlled download endpoints and authorization checks;
  media URLs must not be exposed directly.
- PeopleHub has login only. Employees and interns cannot self-register.
- The public CareerHub authentication boundary remains independent.

## Responsive and accessible behavior

- Desktop uses a persistent navigation rail and a two-column dashboard.
- Tablet and mobile collapse to one column with an explicit menu control and
  backdrop.
- Action lists remain readable without horizontal scrolling.
- Keyboard focus is visible, headings and landmarks are structured, and status
  is never conveyed by color alone.
- Motion is disabled when `prefers-reduced-motion` is enabled.

## Recommended product roadmap

### Next release

- Notification centre with read/unread status and configurable email summaries.
- Private document centre for contracts, certificates, Learning Agreements, and
  payslips using controlled downloads and document-retention rules.
- Personal-data correction requests instead of unrestricted direct editing.
- Team absence calendar and supervisor approval inbox.
- Work schedule, time-clock status, and missing-attendance correction requests.

### Later, using existing modules or integrations

- Expenses and reimbursements where a formal company policy exists.
- Benefits and company-perks catalogue.
- Security centre showing active sessions, account provisioning status, and a
  route to report suspected access problems.
- Searchable knowledge base, onboarding FAQ, office guide, and service catalogue.
- Recognition and internal opportunities only after governance and visibility
  rules are approved.
- Authentik SSO status and Moodle course launch/progress through their existing
  integration adapters; do not recreate either product inside PeopleHub.

## Explicit non-goals

- No public employee signup.
- No unapproved employee directory fields.
- No automated performance or disciplinary decisions.
- No duplicate employee, leave, attendance, asset, training, or document system.
- No direct integration-side writes to arbitrary application tables.
