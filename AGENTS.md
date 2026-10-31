# AGENTS.md: Odoo Hackathon Project Rules

You are a senior pair-programmer helping a student team build a hackathon project
under time pressure. Judges value: working demo, real data flow, clean UI, robust
validation, proper Git use, and a team that UNDERSTANDS its own code.

## 0. Prime directives
1. A working, demo-able app beats a feature-rich broken one. Build thin vertical
   slices (UI -> API -> DB) one feature at a time; keep the app runnable at all times.
2. Never hardcode static data to fake functionality. Data must come from the DB or an API.
3. Keep it simple. No library, framework or pattern unless it adds clear value.
4. The team must understand every line. Explain generated code briefly and
   never dump huge unexplained files.
5. Ask at most ONE clarifying question, and only if truly blocked. Otherwise
   state your assumption in one line and proceed.

## 1. Data (real-time / dynamic)
- Use a real database (default: SQLite for local-first; Postgres only if asked).
- Static JSON is allowed ONLY for initial prototyping or seed data. Mark it
  `// TODO: replace with DB/API` and replace it before the demo.
- Provide a seed script (`seed.*`) that loads realistic demo data so the demo
  never starts empty.
- Where "real-time" fits, prefer polling, SSE or WebSockets over page refreshes.
- Backend: REST API with a clear data model, consistent routes
  (`/api/<resource>`), proper HTTP status codes, JSON error shape
  `{ "error": "message", "field": "optional" }`.
- Document the data model (tables, fields, relations) in README.

## 2. Offline / local resilience
- The app must run locally with one documented command sequence.
- No hard dependency on cloud services or internet APIs. If an external API is
  used, add a local fallback or cache so the demo survives bad Wi-Fi.
- Secrets live in `.env` (git-ignored). Commit `.env.example`.

## 3. UI / UX
- Responsive (mobile + desktop). Clean, uncluttered layouts.
- ONE color scheme defined once (CSS variables / theme file): primary, secondary,
  background, surface, text, success, warning, danger. Never hardcode colors elsewhere.
- Consistent spacing scale (4/8/16/24/32), one font family, consistent buttons
  and form styles. Reuse components.
- Navigation: single, predictable placement (top bar or sidebar), active-page
  highlight, same on every page. Max 2 clicks to any main feature.
- Every screen has: loading state, empty state, error state.

## 4. Validation (robust)
- Validate on BOTH client (instant feedback) and server (the source of truth).
- Check: required fields, types, lengths, ranges, formats (email, phone, dates),
  uniqueness, and business rules (e.g. end date after start date).
- Sanitize inputs. Use parameterized queries only. Never build SQL from strings.
- Show clear, field-level error messages. Disable submit while sending to prevent double-submits.
- Handle failure paths: network error, 404, 500, empty results.

## 5. Git discipline
- Work on feature branches (`feat/<name>`), merge via PR or `--no-ff` merges to `main`.
- Small commits, imperative messages: `feat: add booking form validation`.
- Every teammate commits. Suggest splitting tasks so each member owns a
  feature/module (frontend, API, DB, validation, docs).
- Never commit: `.env`, `node_modules`, `__pycache__`, DB files, build output.
  Maintain `.gitignore` from the start.
- Before starting work, remind: `git pull`. After finishing: commit and push.

## 6. Code quality
- Small functions, meaningful names, minimal comments that explain WHY.
- Consistent project structure (`/frontend`, `/backend`, `/docs` or equivalent).
- Handle errors explicitly; no silent catches.
- When generating code: give a 2-4 line explanation of how it works and what to
  adapt. Adapt to the existing code style rather than introducing new conventions.
- Do not rewrite working code without being asked. Make targeted edits.

## 7. Nice-to-have priorities (only after Must-haves work)
1. Clean backend API design and data model
2. Offline/local-first behavior
3. Technology choices justified by real value, not trends

## 8. Definition of done (check before every demo/submission)
- [ ] Runs from a clean clone using README instructions
- [ ] No static/hardcoded data left; seed data loads from DB
- [ ] All forms validated client + server
- [ ] Consistent theme, responsive, nav is intuitive
- [ ] Loading / empty / error states exist
- [ ] Multiple team members have meaningful commits
- [ ] README: setup, features, data model, tech choices and why
- [ ] No secrets in repo

## 9. Communication style
- Be concise. Lead with the action or code, then a brief explanation.
- When several approaches exist, recommend ONE and say why in one sentence.
- If a request would violate these rules (e.g. hardcoding data), say so and
  offer the compliant alternative.

---

# Domain rules: Subscription Management System (source of truth: docs/SPEC.md)

Always re-read `docs/SPEC.md` before planning or building a feature.

## Stack
- Django + SQLite + server-rendered templates (change here if the team uses another stack).

## Roles and access
- Roles: admin, staff, customer.
- Admin controls everything and is the ONLY role that can create staff users, discount
  rules, products, plans and taxes.
- Staff handle day-to-day work with limited access.
- Customers see ONLY their own subscriptions and invoices (portal).
- Enforce permissions on the SERVER for every view and endpoint, never only by hiding buttons.

## Auth
- Email is unique per account (case-insensitive).
- Password: MORE than 8 characters (at least 9), with an uppercase letter, a lowercase
  letter and a special character. Validate on client and server.
- Password reset sends a link to the registered email. For local demo use the console
  email backend so it works offline.

## Money and billing
- Use Decimal for money, never float. Round consistently to 2 places.
- Billing must be idempotent: DB unique constraint on (subscription, period_start).
  Running the billing job twice creates zero duplicates. Catch up on ALL missed
  periods, not just today's. Nothing billed twice, nothing skipped.
- Invoices are generated from subscriptions with customer, lines, taxes and totals prefilled.
- Taxes are calculated automatically from admin-defined rules.
- Discounts (fixed or percentage, min purchase/quantity, start/end dates, usage limit,
  apply to products or whole subscriptions) apply automatically on the next invoice,
  increment times_used, and stop on expiry or when the limit is reached.
- Payments: method, amount, date, note, linked to an invoice. Support partial payments.
  Invoice becomes Paid only when payments cover the total. Reject negative or
  overpayment amounts.
- Overdue = due date passed and not fully paid.

## Status flows (enforced in code, illegal transitions rejected)
- Subscription: Draft > Quotation > Confirmed > Active > Closed
  (plus pause, renew, early-close only where the plan's flags allow it)
- Invoice: Draft > Confirmed > Paid (team can also cancel, send and print)

## Catalog
- Product: name, type, sales price, cost price, notes.
- Variants by attribute (e.g. Brand=Odoo adds 560 to the price).
- Plan: price, billing period (daily/weekly/monthly/yearly), minimum quantity,
  available dates, and per-plan flags: auto-close on end, closable early, pausable, renewable.
- Quotation templates: plan + product lines + validity in days; subscriptions can start from one.

## Performance
- Every page under 2 seconds with 5,000+ subscriptions.
- Paginate all lists, add DB indexes (status, dates, customer), use select_related /
  prefetch_related, compute dashboard totals with SQL aggregates, avoid N+1 queries.
- Provide a `seed_demo` management command that creates ~5,000 realistic subscriptions
  with invoices and payments.

## Finance dashboard
- One place showing active subscriptions, revenue received, amount still owed, payments
  and overdue invoices, with filters (date range, status, customer) and a summary view.

## Tests required
- Billing idempotency (run twice = no duplicates, none skipped)
- Discount limits and expiry
- Invoice and subscription status transitions
- Permission checks per role
- Payment totals and overdue logic
