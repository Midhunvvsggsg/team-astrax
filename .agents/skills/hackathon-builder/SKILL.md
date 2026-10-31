---
name: hackathon-builder
description: End-to-end workflow for building a hackathon project fast and cleanly, from idea to scaffold to features to final polish. Use this skill whenever the user mentions the hackathon, a project idea, scaffolding or starting a new app, planning features, adding a feature or CRUD module, wiring frontend to backend, adding validation, preparing a demo, reviewing readiness before submission, or asks "what should I build next", even if they do not say "hackathon" explicitly.
---

# Hackathon Builder

Goal: deliver a working, polished, demo-ready app that meets the judging criteria
(real data, clean responsive UI, robust validation, intuitive navigation, proper Git
use) while the team understands all of its code. Follow `AGENTS.md` at all times.

## Phase 1: Plan (max 10 minutes)

1. Restate the problem in one sentence. Pick ONE core user and ONE core flow.
2. Write the MVP as 3 to 5 features, ordered by demo value. Cut everything else into a
   "later" list.
3. Choose the stack. Default to what the team already knows. If undecided, suggest:
   - Frontend: React + Vite (or plain HTML/CSS/JS for small apps)
   - Backend: Node/Express or Python/Flask or FastAPI
   - DB: SQLite
   State why in one sentence. No trendy extras without clear value.
4. Draft the data model: entities, fields, relationships. Output as a short table list.
5. Split work across teammates by module so everyone commits.

Output a `docs/PLAN.md` with: problem, MVP features, stack and rationale, data model, task split.

## Phase 2: Scaffold

Create this structure (adapt names to stack):

```
project/
├── frontend/        UI, theme file, reusable components
├── backend/         routes, models, validators, seed script
├── docs/            PLAN.md, API.md
├── .env.example
├── .gitignore
└── README.md
```

Scaffold checklist:
- `.gitignore` and `.env.example` created before the first commit
- Theme file with all color/spacing/font tokens defined once
- Layout component with the navigation (consistent on every page)
- DB connection, model definitions, `seed` script with realistic demo data
- Health route (`GET /api/health`) to confirm the backend is reachable
- First commit on `main`, then feature branches

## Phase 3: Build features as vertical slices

For each MVP feature, in this order:

1. **Model**: table/schema plus seed rows
2. **API**: endpoints with validation and consistent error shape
3. **UI**: page/component using the shared theme, wired to the real API
4. **States**: loading, empty, error
5. **Validation**: client-side for UX, server-side for truth
6. **Test manually**: happy path, bad input, empty data, network failure
7. **Commit**: `feat: <what>` on a feature branch, then merge

Do not start the next feature until the current one works end to end.

### Validation checklist (apply to every form and endpoint)
- Required, type, min/max length, range
- Format (email, phone, date, URL)
- Uniqueness and referential integrity
- Business rules (date ordering, stock limits, permissions)
- Safe handling: parameterized queries, escaped output, trimmed input
- Clear field-level messages shown next to the input

### API response conventions
- Success: `200/201` with JSON body
- Validation error: `400/422` with `{ "error": "...", "field": "..." }`
- Not found: `404`; server error: `500` with a safe message (no stack traces)

## Phase 4: Polish (reserve the last 20% of time)

1. Consistency pass: colors, spacing, fonts, button styles, nav placement
2. Responsive check at ~375px, ~768px and desktop widths
3. Replace any leftover static/hardcoded data with DB/API data
4. Add offline/local fallbacks for any external API
5. Remove dead code, console logs and unused dependencies
6. Write the README: setup steps, features, data model, tech choices and why

## Phase 5: Demo and submission readiness

Run through the "Definition of done" in `AGENTS.md`, and report pass/fail for each item.
Then prepare:
- A 2-minute demo script following the core user flow
- A reset command (`seed`) so the demo starts from clean realistic data
- 3 likely judge questions with answers (why this stack, how is data handled,
  what would you build next)

## Understanding gate (important)

After generating any non-trivial code, add a short "How it works" note (2 to 4
lines) and one "You may want to adapt" note. If the user pastes code they do not
understand, explain it line by line before building on it. The team must be able
to explain every part of the project to the judges.

## When stuck or running low on time
- Cut scope, not quality: drop the lowest-value feature, keep the rest polished.
- Prefer fixing a broken core flow over adding anything new.
- Say what you are cutting and why, in one line.
