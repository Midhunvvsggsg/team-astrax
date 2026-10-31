# Brightloop - Subscription & Recurring Billing Management

Built by **team-astrax** for the Odoo Hackathon.

An enterprise subscription platform powering products with variant pricing, recurring plans, quotation templates, automated idempotent billing, discount engines, and real-time finance reporting.

---

## 🛠️ Tech Stack & Architecture

- **Backend:** Python 3.14 + Django 6.1
- **Database:** SQLite (local-first, zero setup, offline-resilient)
- **Frontend / UI:** Django Server-Rendered Templates + Unified Theme Tokens (CSS variables)
- **Email:** Django Console Backend (100% offline hackathon resilience)
- **Security:** Strict password validator, case-insensitive email uniqueness, server-side RBAC decorators/mixins

---

## 🚀 Quick Setup & Run Instructions

### 1. Clone & Enter Project Directory
```bash
git clone <repo-url>
cd team-astrax
```

### 2. Set Up Virtual Environment & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Variables & Database Migration
```bash
cp .env.example .env
python manage.py migrate
```

### 4. Seed First Administrator
Create the root admin account using our custom management command:
```bash
python manage.py create_admin --email admin@brightloop.com --password "Admin@12345"
```

### 5. Run the Local Development Server
```bash
python manage.py runserver
```
Visit the application in your browser at: **`http://127.0.0.1:8000/`**

---

## 🔑 Demo Accounts

| Role | Email | Password | Access Scope |
|---|---|---|---|
| **Admin** | `admin@brightloop.com` | `Admin@12345` | Full system control: Manage staff, products, plans, taxes, discounts, financial reports |
| **Staff** | *Created via Admin console* | *(Per staff creation)* | Operations: Manage customer subscriptions, confirm invoices, register payments |
| **Customer** | *Created via `/accounts/signup/`* | *(Self-service)* | Portal: View own subscriptions, inspect invoices, submit payments |

---

## 🧪 Running Automated Tests

Run the full automated test suite (includes model integrity, RBAC server enforcement, password validation, case-insensitive auth):
```bash
python manage.py test
```

---

## 📐 Implemented Features

### Phase 2: Scaffolding, RBAC & Authentication
- [x] **Custom User Model:** Unique case-insensitive email login, three distinct roles (`admin`, `staff`, `customer`).
- [x] **Customer Signup:** Strictly registers customers only; instant client-side validation checklist + server-side enforcement (>8 characters, uppercase, lowercase, special character).
- [x] **Authentication:** Case-insensitive email login, logout, and password reset via `console.EmailBackend`.
- [x] **Server-Enforced RBAC:** `@admin_required`, `@staff_or_admin_required`, and `@customer_required` decorators and CBV mixins.
- [x] **Admin Staff Console:** Admin-only view to provision internal operational staff members.
- [x] **Unified Theme System:** Responsive layout with centralized design tokens (`static/css/theme.css`), active-link navbar, loading spinners, empty states, and field-level error messages.

### Phase 3: Product Catalog, Variants, Taxes & Plans
- [x] **Products & Pricing:** Name, type, Decimal sales price, Decimal cost price, notes, active status.
- [x] **Pricing Variants:** Attributes (e.g. Brand) and values (e.g. Odoo) with extra prices (e.g. +$560.00). Dynamic property calculates final price (`base + extra`).
- [x] **Tax Engine Rules:** Admin-configured taxes with percentage rates, optional fixed fees, and automatic tax calculation helper.
- [x] **Subscription Plans:** Daily, Weekly, Monthly, and Yearly billing periods, minimum seat quantity, date availability windows, and lifecycle flags (`auto_close_on_end`, `closable_early`, `pausable`, `renewable`).
- [x] **Strict Role Permissions:** Admin-only create/edit/delete; Staff view-only access; Customers blocked.
- [x] **Performance & UX:** Search, filter, pagination (10 items/page), and database indexes on commonly queried fields.

### Phase 4: Quotations & Subscription Lifecycle Management
- [x] **Quotation Templates:** Name, associated plan, product lines (product, variant, quantity, unit price, tax), and validity in days.
- [x] **Quick Turn-to-Subscription:** "Create subscription from template" action automatically pre-fills plan and lines, sets expiry date based on template validity days.
- [x] **Sequential Unique Identifiers:** Auto-generated human-readable numbers (`SUB-00001`, `SUB-00002`...).
- [x] **Enforced Status Transitions:** `Draft` > `Quotation` > `Confirmed` > `Active` > `Closed`. Illegal jumps or reopenings of closed subscriptions are blocked with clear messages.
- [x] **Status Audit History:** Logs who changed status, timestamp, from/to states, and transition notes.
- [x] **Plan Flag Controls:** Pause (requires `pausable`), renew (requires `renewable`), and early close (requires `closable_early`) enforced strictly in code.
- [x] **Minimum Quantity Check:** Total seats across subscription lines must meet the plan's minimum quantity before confirmation/activation.
- [x] **Automated Expiry Closure Command:** `python manage.py close_expired_subscriptions` closes subscriptions past expiry date when `auto_close_on_end` is enabled.
- [x] **Customer Portal Isolation:** Customers see only their own active and past subscriptions in read-only view.

### Phase 5: Promotional Discounts Engine
- [x] **Discount Rules Model:** Name, type (percentage or fixed amount), value, minimum purchase amount, minimum seat quantity, start/end date windows, usage limits, and times_used counters.
- [x] **Scope Granularity:** Supports both whole-subscription discounts and specific product line discounts.
- [x] **Reusable Evaluation Service:** `get_applicable_discounts(subscription, billing_date)` checks active status, dates, limits, minimums, and scope; guarantees percentage $\le 100\%$ and caps fixed discounts at eligible amount (never negative).
- [x] **Admin-Only Management & Staff Read-Only:** Admin CRUD with client/server validation; Staff can inspect rules and eligibility; Customers forbidden.

### Phase 6: Automated Recurring Billing & Invoice Generation
- [x] **Invoicing Models:** `Invoice` and `InvoiceLine` with sequential numbering (`INV-00001`), payment due dates from terms, statuses (`Draft`, `Confirmed`, `Paid`, `Cancelled`), and financial totals.
- [x] **Concurrency & Idempotency Guarantee:** DB-level unique constraint on `(subscription, period_start)` ensures double billing is physically impossible.
- [x] **Automated Catch-Up Engine:** `generate_invoices(as_of_date)` identifies all active subscriptions, automatically detects all unbilled and missed past periods, and generates sequential invoices in chronological order.
- [x] **Taxes & Applied Discounts:** Prefills line items, calculates taxes from active tax rules, applies eligible discounts via `get_applicable_discounts`, and increments discount `times_used` exactly once.
- [x] **Robust Month-End Handling:** Automatically clamps next billing dates to calendar month-ends (e.g. Jan 31 $\rightarrow$ Feb 28, Feb 29 on leap years).
- [x] **Interactive & CLI Billing Run:** Admin/Staff "Run Billing Engine" web button with customizable target date, plus `python manage.py generate_invoices` command with created/skipped/failed metrics.
- [x] **Printable Invoices:** Clean `@media print` layout ready for PDF export or paper printing.

### Phase 7: Invoice Lifecycle Management & Console Email Delivery
- [x] **State Machine Enforcement:** Strict `Draft` → `Confirmed` → `Paid` transitions; cancellation permitted only for unpaid invoices; illegal transitions rejected.
- [x] **Console Email Delivery:** Safe local console backend simulates email sending without external SMTP dependencies, automatically timestamping `sent_at`.
- [x] **Invoice Filtering & Search:** Search by invoice number, customer name, date range, payment status (`Paid`, `Outstanding`, `Overdue`).

### Phase 8: Multi-Method Payment Recording & Overdue Tracking
- [x] **Flexible Payment Methods:** Cash, Bank Transfer, Credit/Debit Card, UPI, and Cheque.
- [x] **Partial & Full Settlement:** Supports multiple partial payments; transitions invoice to `Paid` automatically once accumulated payments equal or exceed total amount.
- [x] **Validation Rules:** Negative payments, zero amounts, and overpayments strictly blocked; payments only accepted on `Confirmed` invoices.
- [x] **Zero N+1 Query Aggregates:** `amount_paid` and `amount_due` calculated directly via SQL `Sum` annotations.

### Phase 9: Real-time Executive Finance Intelligence Dashboard & Local Offline Charts
- [x] **KPI Cards:** Active Subscriptions, Total Revenue Received, Amount Still Owed, Overdue Invoices Count, and Monthly Recurring Revenue (MRR).
- [x] **SQL-Only Aggregates:** Database-level aggregations (`Sum`, `Count`, `Case/When`, `Coalesce`) for instant calculation across thousands of records.
- [x] **100% Offline Chart Visualizations:** Chart.js bundled locally (`static/js/chart.umd.min.js`) with zero CDN or external internet dependencies.

### Phase 10: High-Performance Demo Seeder & Latency Optimization
- [x] **Repeatable Demo Seeder (`seed_demo`):** Generates 5,000 subscriptions, 9,800+ invoices, 1,100+ payments, and 200 customers in ~35 seconds using chunked `bulk_create` and pre-computed password hashes.
- [x] **Sub-Second Performance Benchmark:** All platform pages respond in under 60 milliseconds with 5,000+ active records.
- [x] **Automated Query Bounds Testing:** Integrated unit tests prevent N+1 regressions across paginated lists.

### Phase 11: Customer Self-Service Portal & Strict Isolation
- [x] **Scoped Query Isolation:** Every customer query is strictly filtered by `request.user`; any attempt to access another customer's subscription or invoice by ID returns `404 Not Found`.
- [x] **Subscription Actions & Next Billing Date:** Shows plan name, status, upcoming billing date, expiry, and exposes permitted lifecycle actions (`Pause`, `Resume`, `Renew`, `Cancel/Close`) strictly governed by plan flags.
- [x] **Invoices & Payment History:** Filter invoices by status (`Paid`, `Outstanding`, `Overdue`, `Draft`, `Confirmed`), view printable PDF-ready layout, and inspect recent payment transactions.
- [x] **Profile & Password Management:** Update first/last name and unique login email; change password with real-time feedback enforcing the hackathon security rules (>8 chars, uppercase, lowercase, special char).

---

## 👥 Contributors
- **Midhun** ([@Midhunvvsggsg](https://github.com/Midhunvvsggsg))
- **arjunsagar-ai** ([@arjunsagar-ai](https://github.com/arjunsagar-ai))

