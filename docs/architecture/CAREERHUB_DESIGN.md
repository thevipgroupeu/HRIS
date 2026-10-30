# CareerHub Design System

## Source and capture context

The CareerHub visual system was derived from the current official THEVIPGROUP properties on 2026-10-25:

- [THEVIPGROUP homepage](https://thevipgroup.eu/) — desktop full-page capture at 1440 px, branding extraction and content hierarchy.
- [FluxOps product page](https://thevipgroup.eu/products/fluxops/) — desktop full-page capture at 1440 px and venture palette/component treatment.
- [FindMyStay product page](https://thevipgroup.eu/products/findmystay/) — desktop full-page capture at 1440 px and venture palette/component treatment.

The screenshots were inspected visually, not inferred from metadata alone. CareerHub adapts the system for a denser search and reading task; it is not a pixel-for-pixel recreation.

## Observed system

- Poppins is used for both body and headings, with heavy, tightly tracked display headings.
- THEVIPGROUP uses navy `#07112c`, group blue near `#14338c`, an off-white `#f7f8fb` canvas, thin cool-grey rules and strong white space.
- Primary group actions are squared and dark; supporting product pages also use compact rounded controls. CareerHub keeps squared actions for consistency and clarity.
- Layouts use large editorial headings, small uppercase technical labels, split section introductions, fine grid lines and low/no-shadow content cards.
- Group identity is expressed with short blue, magenta and green lines rather than decorative illustration.
- FluxOps uses a deep industrial navy with teal/green operational accents and pale blue-green surfaces.
- FindMyStay uses deep charcoal/navy with teal as its principal action colour and small warm orange accents.

## CareerHub tokens

| Role | Value | Origin/status |
|---|---|---|
| Group navy | `#07112c` | Extracted from official group/FluxOps pages |
| Group blue | `#14338c` | Extracted |
| Canvas | `#f7f8fb` | Extracted |
| Body ink | `#10182c` | Proposed accessible near-navy |
| Muted text | `#596377` | Proposed for readable secondary copy |
| Rule | `#dfe4ed` | Visual estimate from inspected pages |
| THEVIPGROUP magenta | `#bd3fb2` | Accessibility-adjusted from observed accent |
| THEVIPGROUP green | `#397e28` | Accessibility-adjusted from observed accent |
| FluxOps accent | `#087f69` | Accessibility-adjusted industrial teal |
| FindMyStay accent | `#217f75` | Extracted |
| FindMyStay warm accent | `#bb541a` | Accessibility-adjusted from observed orange |
| Focus ring | `#9a2f90` | Proposed high-visibility brand-compatible focus colour |

Spacing uses a practical 4 px base with primary increments at 8, 12, 16, 24, 32, 48, 64 and 96 px. The content shell is capped at 1180 px. Mobile side gutters are 16 px and increase to 32 px at 768 px.

Poppins is served from the existing repository font asset at `payroll/static/payroll/fonts/Poppins_Regular.ttf`; no third-party font request is made. The single local face is an implementation constraint, so heavier weights are browser-synthesised. A licensed multi-weight self-hosted family can replace it later without changing the token contract.

## Layout and components

- Mobile-first single-column pages; primary two/three-column grids start at 768 px and wide role layouts at 1152 px.
- Sticky white navigation with a keyboard-native `<details>` menu on small screens and a compact desktop navigation on larger screens.
- Job cards use one venture-coloured top rule, role metadata chips, explicit application status and one dominant detail link.
- Filters are a native GET form with persistent values, visible labels and no JavaScript dependency.
- Detail pages place public role content in the main column and an application summary/CTA in a bounded side panel.
- FAQs use native `<details>` disclosure widgets.
- Buttons, links and controls have visible focus indicators. Reduced-motion preferences remove animated transitions and smooth scrolling.
- All pages include a skip link, semantic landmarks, one H1, canonical URL, description and Open Graph metadata. Job details add nonce-protected `JobPosting` JSON-LD.

## Imagery and brand assets

The reference sites use the official multicolour group emblem, FluxOps product UI screens and a FindMyStay product glow/device composition. CareerHub intentionally does not copy product screenshots into recruitment pages: role information and navigation are the primary task. The header uses a compact inline group mark derived from the observed blue/magenta/green emblem treatment, avoiding an external image dependency.

## Accessibility guidance

- Maintain at least WCAG AA contrast for body text, controls and status labels.
- Never communicate venture or status through colour alone; every marker carries text.
- Keep filter labels visible and preserve logical source order at every breakpoint.
- Do not remove browser focus outlines unless the replacement remains clearly visible.
- Application closure is enforced by the server-calculated `accepting_applications` value, not only by disabled styling.
- Verify desktop and mobile layouts in a real browser after material template or CSS changes.

## Browser review evidence

The implemented site was reviewed in headless Microsoft Edge at 1440 x 1050 and
390 x 844 against a disposable SQLite database containing only the three
synthetic demonstration roles. The automated review confirmed three live API
results, one matching multi-filter result, keyboard focus on the mobile menu,
one visible label per filter control, no horizontal overflow, no browser console
errors, job `JSON-LD`, and a `404` response for `/admin/` on the public process.

- [CareerHub landing page, desktop](screenshots/careerhub-landing-desktop.png)
- [FluxOps role detail, desktop](screenshots/careerhub-role-desktop.png)
- [Opportunities and filters, mobile](screenshots/careerhub-opportunities-mobile.png)

## Open design items

- Confirm whether the official brand team can supply a versioned local SVG mark and the complete licensed Poppins weight set.
- Candidate-account signup, validation, verification, login, recovery and profile patterns are implemented. The candidate dashboard now renders private pre-interview verification requests and validated upload actions; production activation remains blocked on an approved malware scanner and retention process.
- Localised Polish copy and language switching are not part of this phase.
