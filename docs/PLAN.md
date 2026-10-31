# Hackathon Plan: Brightloop Subscription Management System

## 1. Problem Statement
Brightloop Software needs an integrated subscription & recurring billing platform that automates the lifecycle of subscriptions—from product/plan definition and quotation to idempotent invoice generation, payment processing, customer self-service, and real-time financial reporting.

**Core User:** Business Admin / Staff & Customer  
**Core Flow:** Configure Plans & Products -> Create Quotation / Subscription -> Automated Idempotent Billing -> Record Payments -> Real-Time Finance Analytics.

---

## 2. Tech Stack & Rationale
- **Framework:** Python / Django (as specified in `AGENTS.md`)
- **Database:** SQLite (fast, local-first, zero-setup, reliable for offline judging)
- **Frontend / UI:** Django Server-Rendered Templates + Tailwind / Semantic CSS styling (single responsive design token system, zero complex JS bundlers, instant load times <2s)
- **Auth:** Django custom User model with strict password validation & role-based access control (Admin, Staff, Customer)
- **Email:** Django Console Backend (100% offline-resilient for password resets)

---

## 3. MVP Features (Ordered by Demo Value)

1. **Authentication & Role-Based Access Control:**
   - Admin, Staff, and Customer roles with server-side permission enforcement.
   - Strict password validation (>8 chars, uppercase, lowercase, special char) and unique email.
   - Dedicated views: Admin/Staff management vs. Customer self-service portal.

2. **Product Catalog & Subscription Plans:**
   - Products with types, cost/sales price, and attribute variants (e.g., Brand=Odoo adds 560).
   - Plans with frequencies (Daily, Weekly, Monthly, Yearly), minimum quantities, availability dates, and flags (closable early, pausable, renewable, auto-close).
   - Quotation templates with validity periods and line items.

3. **Subscription Lifecycle & Quotation Flow:**
   - Status flow: `Draft` -> `Quotation` -> `Confirmed` -> `Active` -> `Closed` (plus pause, renew, early close).
   - Quotation to active subscription conversion with calculated line totals.

4. **Automated Idempotent Billing & Invoicing Engine:**
   - Generates invoices with prefilled lines, calculated taxes, and applied discounts.
   - **Idempotency Guarantee:** DB unique constraint on `(subscription, period_start)`.
   - Missed period catch-up logic (bills all unbilled past periods, zero duplicates, none skipped).
   - Invoice status flow: `Draft` -> `Confirmed` -> `Paid` (cancel, send, print).

5. **Discounts & Payment Processing:**
   - Discount rules (fixed or percentage, min purchase/quantity, validity dates, usage limit).
   - Payments with method, date, amount, and notes. Supports partial payments, auto-transitions invoice to `Paid` once fully covered.
   - Overdue calculation (past due date & unpaid balance > 0).

6. **Finance Dashboard & High Performance (5,000+ Records):**
   - Summary cards: Active subscriptions, total revenue received, outstanding debt, overdue invoices.
   - Filterable lists (by date, status, customer) with SQL aggregation and pagination.
   - Sub-2-second response times using `select_related`, `prefetch_related`, and DB indices.
   - `python manage.py seed_demo` command populating 5,000+ realistic subscriptions and invoices.

---

## 4. Data Model Overview

| Entity | Key Fields | Relationships |
|---|---|---|
| `User` | email, role (admin, staff, customer), is_active | Base user model |
| `Product` | name, product_type, sales_price, cost_price, notes | One-to-many with ProductVariant |
| `ProductVariant` | product_id, attribute_name, attribute_value, price_extra | Belongs to Product |
| `Plan` | name, price, billing_period (D/W/M/Y), min_qty, available_from, available_to, auto_close, closable_early, pausable, renewable | Associated with subscriptions |
| `QuotationTemplate`| name, plan_id, validity_days | Template for quick quotes |
| `QuotationTemplateLine`| template_id, product_id, quantity, unit_price | Belongs to QuotationTemplate |
| `TaxRule` | name, rate_percentage, is_active | Applied to lines/invoices |
| `DiscountRule` | code, discount_type (fixed/percent), value, min_purchase, min_quantity, start_date, end_date, max_uses, times_used, applies_to_product_id | Applied during billing |
| `Subscription` | sub_number, customer_id, plan_id, status (Draft/Quotation/Confirmed/Active/Closed/Paused), start_date, expiry_date, next_billing_date, payment_terms | Belongs to User (Customer) & Plan |
| `SubscriptionLine` | subscription_id, product_id, variant_id, quantity, unit_price | Belongs to Subscription |
| `Invoice` | invoice_number, subscription_id, customer_id, period_start, period_end, status (Draft/Confirmed/Paid/Cancelled), subtotal, tax_amount, discount_amount, total_amount, due_date | **Unique `(subscription, period_start)`** |
| `InvoiceLine` | invoice_id, product_id, description, quantity, unit_price, tax_id, line_total | Belongs to Invoice |
| `Payment` | invoice_id, amount, payment_date, payment_method, note, recorded_by_id | Belongs to Invoice |

---

## 5. Team Task Split (Suggested for team-astrax)
- **Member 1 (Auth & Customer Portal):** Custom user model, password validator, roles/permissions middleware, customer portal views.
- **Member 2 (Catalog & Subscriptions):** Products, variants, plans, quotation templates, subscription lifecycle transitions.
- **Member 3 (Billing & Payment Engine):** Idempotent billing command/service, tax engine, discount rules, payment recording, overdue check.
- **Member 4 (Finance Dashboard, Seeding & Polish):** Aggregated finance dashboard with filters, `seed_demo` (5k items), UI theme consistency, README & automated tests.
