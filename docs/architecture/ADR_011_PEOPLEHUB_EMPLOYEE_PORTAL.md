# ADR-011: Restricted PeopleHub employee portal

Date: 2026-10-27  
Status: Accepted for the Phase 12 implementation slice

## Context

Employees and interns need one private dashboard at `people.thevipgroup.eu`, but
Horilla already owns employee, leave, attendance, asset, project, performance,
policy, helpdesk and permission records. Rebuilding those modules would create
conflicting business truth. Serving the complete Horilla URL tree on the employee
hostname would also let ordinary users probe HR administration endpoints.

## Decision

- `people.thevipgroup.eu` runs a separate `peoplehub_wsgi` process with its own
  root URLconf and cookie names. That URLconf mounts only PeopleHub login,
  dashboard, directory, policy, leave, request and team routes; native HR admin,
  broad API and media routes are absent.
- PeopleHub reads native `EmployeeWorkInformation`, `Attendance`, `AvailableLeave`,
  `LeaveRequest`, `AssetAssignment`, `Task`, `EmployeeObjective`, `Feedback`,
  `Policy` and helpdesk `Ticket` records. Leave and internal-request submission
  create those same native records.
- `tvg_peoplehub` owns only additive links Horilla lacks: employee-to-Venture
  assignment, approved portal request routing, learning/reporting presentation
  metadata, native-ticket links and minimal immutable audit events.
- Shared directory rows are constructed from an explicit seven-field allowlist:
  name, work email, position, department, venture, manager and work location.
  Personal email, phone, address, birth/identity, medical and payroll fields are
  never supplied to the template.
- Every employee detail action first enforces legal-employer equality. Employees
  can see themselves, supervisors can see direct reports, and broader team access
  requires the existing `employee.view_employee` permission. Request queues
  require native `helpdesk.view_ticket` and remain company-scoped.
- Role-aware navigation is a projection of direct-report relationships plus
  existing company-scoped Horilla permissions; it does not create a second role
  or authorization system.
- Relevant native policies are filtered by company, employee, department and job
  position. Accommodation support appears only where the linked Erasmus case
  already records a minimal assistance request.
- Approved request routes are immutable. Changes require a replacement route,
  avoiding silent redirection of existing requests.
- Local Horilla employee authentication is the initial mechanism. An adapter
  interface and disabled authentik provider are configurable, but no OIDC claims,
  endpoints or provisioning behavior are invented and authentik is not required.
- Portal templates bypass Horilla's global HR navigation context processors, and
  custom 403/404 responses cannot fall back into the administrative menu system.

## Consequences

The implementation remains compatible with upstream Horilla data and permission
semantics while presenting a narrower employee experience. The PeopleOps runtime
still performs administrative configuration and schema migration; the PeopleHub
container does neither. A real authentik connection, Moodle synchronization,
outbound notifications and additional workflow automation remain future adapter
work requiring approved contracts and secrets handling.
