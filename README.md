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
