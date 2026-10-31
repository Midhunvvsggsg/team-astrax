# Brightloop - 2-Minute Demo Script & Judge Q&A Guide

Welcome to **Brightloop Software** — the digital backbone for recurring subscription businesses. This guide provides a rapid 2-minute demo script walking through a customer's monthly lifecycle, followed by concise answers to 8 likely judge questions.

---

## ⏱️ 2-Minute Live Demo Script

### Act 1: The New Customer Signs Up (0:00 - 0:20)
1. **Open:** `http://127.0.0.1:8000/accounts/signup/`
2. **Action:** Register as a new customer (`acme@corp.com`, name: "Acme Corp", password: `CustomerPass@123`).
3. **Showcase:**
   - Real-time password validator enforcing the security rules (>8 chars, uppercase, lowercase, special character).
   - Form disables submit to prevent double-submits.
   - Account is created strictly with `role=customer`.
4. **Login:** Log in with `acme@corp.com`. Customer lands on the **Customer Portal** (`/accounts/portal/`) — clean, personalized, showing empty states for active subscriptions and invoices.

---

### Act 2: Turning a Quote into a Subscription (0:20 - 0:45)
1. **Switch Session:** Open incognito or log in as operational staff (`staff@brightloop.com` / `Staff@12345`).
2. **Navigate:** Go to **Subscriptions** > **Quotation Templates** (`/subscriptions/templates/`).
3. **Action:** Click "Create Subscription from Template" (`/subscriptions/from-template/`):
   - Template: "Enterprise Quote Template" (prefilled with plan & products).
   - Customer: Select `acme@corp.com`.
   - Start Date: Today.
4. **Showcase:**
   - Pre-fills plan, copies line items (e.g. 2x Cloud ERP with Odoo brand variant pricing +$560 and 10% VAT).
   - Calculates expiry date dynamically from template's validity days (e.g. 15 days).
   - Generates sequential unique identifier: `SUB-XXXXX`.
   - Initial status: `Quotation`.

---

### Act 3: Enforced Lifecycle State Machine (0:45 - 1:00)
1. **Navigate:** Open the generated subscription detail view (`/subscriptions/<id>/`).
2. **Action:** Walk through state progression:
   - Click **"Accept & Confirm"** → status transitions to `Confirmed`.
   - Click **"Activate Subscription"** → status transitions to `Active`.
3. **Showcase:**
   - Audit trail logged in real time to the **Status History Timeline** (who, when, previous/next states, notes).
   - Enforces plan flags: pause/resume and renew action buttons only appear if the plan permits them.

---

### Act 4: Billing Day & Automatic Discounts (1:00 - 1:20)
1. **Navigate:** Go to **Invoices** (`/invoices/`).
2. **Action:** Click **"Run Billing Engine"** (`/invoices/run-billing/`):
   - Target date: Today.
3. **Showcase:**
   - Invoices generated automatically in chronological order for active subscriptions.
   - **Discount Engine:** Active 10% discount rule is automatically evaluated, applied as a negative line item, and increments discount usage counter.
   - Taxes are computed automatically from tax rules.
   - **Idempotency Proof:** Click "Run Billing Engine" a second time for the same date. Result: `0 created, 1 up-to-date` — zero duplicate invoices.

---

### Act 5: Invoice Delivery & Multi-Method Payments (1:20 - 1:40)
1. **Navigate:** Open generated invoice (`INV-XXXXX`).
2. **Action:**
   - Click **"Confirm Invoice"** → status moves to `Confirmed`.
   - Click **"Send to Customer"** → triggers console email backend (100% offline-safe) and records `sent_at`.
   - Click **"Print"** → displays clean, media-print optimized PDF layout.
3. **Payment Collection:**
   - Record partial payment of $500 via **Bank Transfer** (remaining balance dynamically updates via SQL aggregates).
   - Record remaining balance via **UPI**.
   - Invoice automatically transitions to `Paid in Full` once payments cover the total. Overpayments and negative payments are strictly rejected.

---

### Act 6: Executive Dashboard & Customer Self-Service (1:40 - 2:00)
1. **Staff View:** Navigate to **Executive Finance Dashboard** (`/invoices/dashboard/`):
   - Instant metrics computed via database SQL aggregations: MRR, ARR, Active Subscriptions, Total Revenue, Collection Rate, and Overdue Invoices.
   - 100% offline chart visualizations (bundled local Chart.js).
2. **Customer View:** Log back into customer account (`acme@corp.com`):
   - Opens **Customer Portal** (`/accounts/portal/`).
   - Customer sees their active subscription, next billing date, paid invoice, and verified payment history.
   - Tests cross-customer isolation: attempts to visit another customer's invoice or subscription by ID in the URL bar → returns strict **HTTP 404 Not Found**.

---

## 💡 8 Likely Judge Questions & Answers

### Q1: How do you guarantee billing idempotency? What happens if the billing job runs twice?
> **Answer:** Idempotency is enforced at two distinct layers. At the database layer, a composite unique constraint on `(subscription, period_start)` makes duplicate invoices physically impossible. At the service layer, `generate_invoices()` pre-queries existing period starts in `sub.invoices.values_list('period_start', flat=True)` and skips periods already billed. Re-running the billing engine produces zero duplicates.

### Q2: How does your system handle catch-up for past unbilled periods or server downtime?
> **Answer:** Rather than only billing for the current calendar day, `generate_invoices(as_of_date)` detects all missed past periods since the subscription's start date (or last billed invoice) and generates chronological invoices period-by-period until the subscription is completely caught up.

### Q3: How do you guarantee customer data privacy and prevent cross-customer access?
> **Answer:** We enforce strict query scoping on the server. In customer-facing views, queries filter directly on `request.user` (e.g. `request.user.subscriptions.all()` or `get_object_or_404(Invoice.objects.filter(customer=request.user), pk=pk)`). If a customer tries to guess or tamper with another customer's ID in the URL, the system returns `404 Not Found` rather than `403 Forbidden`, preventing resource existence disclosure.

### Q4: How are discounts evaluated and what prevents negative invoice totals?
> **Answer:** `get_applicable_discounts()` checks active status, date validity, remaining usage limits, minimum quantities/purchase amounts, and scope (whole subscription vs. specific products). In our mathematical service, percentage discounts are capped at 100% and fixed discounts are bounded by the eligible subtotal, guaranteeing invoices can never have negative totals.

### Q5: Why SQLite instead of PostgreSQL for this hackathon?
> **Answer:** SQLite delivers 100% offline-first resilience with zero external service dependencies or cloud credential requirements. It satisfies all ACID constraints, runs embedded in-process with sub-millisecond query latencies, and allowed us to seed 5,000 subscriptions and 9,800+ invoices in ~35 seconds using chunked bulk operations.

### Q6: How do you achieve sub-60ms page load speeds across 5,000+ active subscriptions?
> **Answer:** We eliminated N+1 queries through strategic `select_related` and `prefetch_related` joins, added composite database indexes across commonly queried foreign keys and date/status fields, and computed financial totals (MRR, revenue, payments, amounts due) entirely in database SQL aggregates using `Sum`, `Count`, and `Case/When`.

### Q7: How are product variants priced and how does choosing a variant affect lines and invoices?
> **Answer:** Each `ProductVariant` specifies an `extra_price` associated with an attribute (e.g. Brand=Odoo adds $560.00). When added to a quotation template or subscription line, the unit price automatically computes `product.sales_price + variant.extra_price` while allowing operational staff to customize it if needed.

### Q8: How does the system handle subscription expiry and plan lifecycle permissions?
> **Answer:** Plans define granular boolean policy flags: `pausable`, `renewable`, `closable_early`, and `auto_close_on_end`. The state machine rejects actions not permitted by the plan. For automated lifecycle management, the `close_expired_subscriptions` management command safely transitions active and paused subscriptions past their expiry date to `Closed` if `auto_close_on_end` is enabled.
