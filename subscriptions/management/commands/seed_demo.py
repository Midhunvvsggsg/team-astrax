import random
from datetime import date, datetime, timedelta
from decimal import Decimal
import time

from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from catalog.models import Product, ProductVariant, Tax, Plan
from subscriptions.models import Subscription, SubscriptionLine, DiscountRule
from invoicing.models import Invoice, InvoiceLine, Payment
from invoicing.services import generate_invoices

class Command(BaseCommand):
    help = "Seed realistic demo data: ~5,000 subscriptions, 200 customers, invoices, and payments."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Wipe existing demo data before seeding.',
        )
        parser.add_argument(
            '--subscriptions',
            type=int,
            default=5000,
            help='Number of subscriptions to generate (default: 5000).',
        )

    def handle(self, *args, **options):
        start_time = time.time()
        should_reset = options['reset']
        target_subs_count = options['subscriptions']

        random.seed(42)  # Deterministic repeatability

        self.stdout.write(self.style.NOTICE("=== Starting Brightloop Demo Data Seeder ==="))

        if should_reset:
            self.stdout.write("Reset requested. Purging existing application data...")
            with transaction.atomic():
                Payment.objects.all().delete()
                InvoiceLine.objects.all().delete()
                Invoice.objects.all().delete()
                SubscriptionLine.objects.all().delete()
                Subscription.objects.all().delete()
                DiscountRule.objects.all().delete()
                ProductVariant.objects.all().delete()
                Product.objects.all().delete()
                Tax.objects.all().delete()
                Plan.objects.all().delete()
                # Remove customers and demo staff, keep superuser admin
                User.objects.filter(role=User.Role.CUSTOMER).delete()
                User.objects.filter(email__contains='@brightloop.com').exclude(is_superuser=True).delete()
            self.stdout.write(self.style.SUCCESS("Purge complete."))

        today = date.today()

        # ---------------------------------------------------------------------
        # 1. Staff and Admin Accounts
        # ---------------------------------------------------------------------
        self.stdout.write("1. Setting up admin and staff accounts...")
        admin_user, _ = User.objects.get_or_create(
            email='admin@brightloop.com',
            defaults={
                'first_name': 'Chief',
                'last_name': 'Admin',
                'role': User.Role.ADMIN,
                'is_staff': True,
                'is_superuser': True,
                'password': make_password('Admin@12345'),
            }
        )
        if not admin_user.is_superuser:
            admin_user.is_superuser = True
            admin_user.is_staff = True
            admin_user.role = User.Role.ADMIN
            admin_user.save()

        staff_data = [
            ('sarah.finance@brightloop.com', 'Sarah', 'Jenkins', User.Role.STAFF),
            ('alex.ops@brightloop.com', 'Alex', 'Rivera', User.Role.STAFF),
            ('support.lead@brightloop.com', 'Marcus', 'Vance', User.Role.STAFF),
        ]
        staff_users = []
        for email, fn, ln, role in staff_data:
            st, _ = User.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': fn,
                    'last_name': ln,
                    'role': role,
                    'is_staff': True,
                    'password': make_password('StaffPassword@1'),
                }
            )
            staff_users.append(st)

        # ---------------------------------------------------------------------
        # 2. 200 Customer Accounts
        # ---------------------------------------------------------------------
        self.stdout.write("2. Creating 200 realistic customer organizations...")
        existing_customers = list(User.objects.filter(role=User.Role.CUSTOMER))
        if len(existing_customers) < 200:
            cust_to_create = []
            hashed_pwd = make_password('CustomerPassword@1')
            companies = [
                "Acme Global", "Nexus AI", "Starlight Systems", "Apex Analytics",
                "CloudWave Tech", "Vanguard Cloud", "Horizon Media", "BluePeak Labs",
                "Titan Logistics", "OmniCorp Services", "Hyperion Data", "Synergy Health",
                "Quantum Dynamics", "Zenith Energy", "Pulse Interactive", "Astra Financial",
                "Crestline Bio", "Velocity Robotics", "Pinnacle Media", "Solstice Cyber"
            ]
            for i in range(len(existing_customers) + 1, 201):
                company = companies[(i - 1) % len(companies)]
                cust_to_create.append(User(
                    email=f"customer_{i:03d}@example.com",
                    first_name=f"{company} #{i}",
                    last_name="Account",
                    role=User.Role.CUSTOMER,
                    password=hashed_pwd,
                ))
            User.objects.bulk_create(cust_to_create, batch_size=200)
            customers = list(User.objects.filter(role=User.Role.CUSTOMER))
        else:
            customers = existing_customers
        self.stdout.write(f"   Available customers: {len(customers)}")

        # ---------------------------------------------------------------------
        # 3. Catalog Plans
        # ---------------------------------------------------------------------
        self.stdout.write("3. Configuring subscription plans...")
        plans_data = [
            ("Starter Cloud Monthly", Decimal('49.00'), Plan.BillingPeriod.MONTHLY, 1, True, True, True, False),
            ("Pro Scale Monthly", Decimal('199.00'), Plan.BillingPeriod.MONTHLY, 1, True, True, True, False),
            ("Enterprise Dedicated Monthly", Decimal('599.00'), Plan.BillingPeriod.MONTHLY, 5, True, True, False, False),
            ("Enterprise Annual Suite", Decimal('4999.00'), Plan.BillingPeriod.YEARLY, 5, False, True, False, False),
            ("Developer Sandbox Weekly", Decimal('19.00'), Plan.BillingPeriod.WEEKLY, 1, True, True, True, False),
            ("High-Frequency Compute Daily", Decimal('5.00'), Plan.BillingPeriod.DAILY, 1, False, True, True, True),
        ]
        plans = []
        for name, price, period, min_q, pausable, renewable, closable, auto_close in plans_data:
            p, _ = Plan.objects.get_or_create(
                name=name,
                defaults={
                    'price': price,
                    'billing_period': period,
                    'minimum_quantity': min_q,
                    'pausable': pausable,
                    'renewable': renewable,
                    'closable_early': closable,
                    'auto_close_on_end': auto_close,
                }
            )
            plans.append(p)

        # ---------------------------------------------------------------------
        # 4. Tax Rules
        # ---------------------------------------------------------------------
        self.stdout.write("4. Configuring tax rules...")
        tax_data = [
            ("Standard VAT (10%)", Decimal('10.00'), Decimal('0.00')),
            ("US Sales Tax (8.25%)", Decimal('8.25'), Decimal('0.00')),
            ("EU Digital Tax (19%)", Decimal('19.00'), Decimal('0.00')),
            ("Zero Tax Exempt (0%)", Decimal('0.00'), Decimal('0.00')),
            ("Infrastructure Surcharge ($2.50)", Decimal('0.00'), Decimal('2.50')),
        ]
        taxes = []
        for name, pct, fixed in tax_data:
            t, _ = Tax.objects.get_or_create(
                name=name,
                defaults={'percentage': pct, 'fixed_amount': fixed, 'is_active': True}
            )
            taxes.append(t)

        # ---------------------------------------------------------------------
        # 5. Products and Attribute Variants
        # ---------------------------------------------------------------------
        self.stdout.write("5. Configuring 15 software products with attribute variants...")
        products_spec = [
            ("User Seat License", Product.ProductType.SOFTWARE, Decimal('25.00'), [("Tier", "Standard", 0), ("Tier", "Advanced", 15), ("Tier", "Power User", 35)]),
            ("Dedicated Cloud Storage", Product.ProductType.ADDON, Decimal('10.00'), [("Capacity", "100GB", 0), ("Capacity", "500GB", 30), ("Capacity", "2TB", 90)]),
            ("Priority 24/7 SLA Support", Product.ProductType.SERVICE, Decimal('150.00'), [("Level", "Business 8x5", 0), ("Level", "Mission-Critical 24x7", 250)]),
            ("API Rate Limit Booster", Product.ProductType.ADDON, Decimal('40.00'), [("Burst", "10k req/min", 0), ("Burst", "50k req/min", 80), ("Burst", "Unlimited", 200)]),
            ("Security & Compliance Pack", Product.ProductType.SERVICE, Decimal('200.00'), [("Standard", "SOC2", 0), ("Standard", "HIPAA", 100), ("Standard", "FedRAMP", 300)]),
            ("Custom Domain & White-label", Product.ProductType.ADDON, Decimal('30.00'), [("Branding", "Custom Domain", 0), ("Branding", "Full White-label", 50)]),
            ("Multi-Region DB Replication", Product.ProductType.SERVICE, Decimal('120.00'), [("Regions", "Dual Region", 0), ("Regions", "Global Quad Region", 180)]),
            ("AI Analytics Copilot", Product.ProductType.SOFTWARE, Decimal('75.00'), [("Model", "Standard GPT", 0), ("Model", "Fine-Tuned Proprietary", 150)]),
            ("Onboarding & Migration Consulting", Product.ProductType.SERVICE, Decimal('500.00'), []),
            ("Automated Backup & Archiving", Product.ProductType.ADDON, Decimal('35.00'), [("Frequency", "Daily", 0), ("Frequency", "Hourly", 45), ("Frequency", "Continuous CDP", 95)]),
            ("Single Sign-On (SSO / SAML)", Product.ProductType.SOFTWARE, Decimal('50.00'), []),
            ("Webhook Event Bus", Product.ProductType.ADDON, Decimal('20.00'), [("Volume", "1M events", 0), ("Volume", "10M events", 60)]),
            ("Dedicated Static IP Pool", Product.ProductType.ADDON, Decimal('15.00'), [("IPs", "1x IPv4", 0), ("IPs", "4x IPv4 Pool", 40)]),
            ("Hardware Security Key (FIDO2)", Product.ProductType.PHYSICAL, Decimal('45.00'), [("Connector", "USB-C", 0), ("Connector", "NFC + USB-C", 15)]),
            ("Enterprise Audit Logging", Product.ProductType.SOFTWARE, Decimal('80.00'), [("Retention", "1 Year", 0), ("Retention", "7 Years Legal Hold", 120)]),
        ]
        products = []
        variants = []
        for prod_name, prod_type, sales_price, var_list in products_spec:
            p, _ = Product.objects.get_or_create(
                name=prod_name,
                defaults={'product_type': prod_type, 'sales_price': sales_price, 'cost_price': (sales_price * Decimal('0.3')).quantize(Decimal('0.01'))}
            )
            products.append(p)
            for attr, val, extra in var_list:
                v, _ = ProductVariant.objects.get_or_create(
                    product=p,
                    attribute=attr,
                    value=val,
                    defaults={'extra_price': Decimal(str(extra))}
                )
                variants.append(v)

        # ---------------------------------------------------------------------
        # 6. Promotional Discounts
        # ---------------------------------------------------------------------
        self.stdout.write("6. Configuring 5 promotional discount rules...")
        discounts_spec = [
            ("WELCOME20", DiscountRule.DiscountType.PERCENTAGE, Decimal('20.00'), DiscountRule.Scope.WHOLE_SUBSCRIPTION, 5000, 0),
            ("ANNUAL15", DiscountRule.DiscountType.PERCENTAGE, Decimal('15.00'), DiscountRule.Scope.WHOLE_SUBSCRIPTION, 1000, 500),
            ("FLAT50OFF", DiscountRule.DiscountType.FIXED, Decimal('50.00'), DiscountRule.Scope.WHOLE_SUBSCRIPTION, 500, 150),
            ("USERSEATS10", DiscountRule.DiscountType.PERCENTAGE, Decimal('10.00'), DiscountRule.Scope.SPECIFIC_PRODUCTS, 1000, 0),
            ("STORAGEPROMO", DiscountRule.DiscountType.FIXED, Decimal('15.00'), DiscountRule.Scope.SPECIFIC_PRODUCTS, 500, 0),
        ]
        created_discounts = []
        for name, dtype, val, scope, limit, min_p in discounts_spec:
            d, _ = DiscountRule.objects.get_or_create(
                name=name,
                defaults={
                    'discount_type': dtype,
                    'value': val,
                    'scope': scope,
                    'usage_limit': limit,
                    'min_purchase_amount': Decimal(str(min_p)),
                    'start_date': today - timedelta(days=365),
                    'end_date': today + timedelta(days=365),
                }
            )
            created_discounts.append(d)

        # Assign product scope
        created_discounts[3].products.set([products[0]])  # User Seats
        created_discounts[4].products.set([products[1]])  # Cloud Storage

        # ---------------------------------------------------------------------
        # 7. Subscriptions Generation (~5,000 in mixed statuses)
        # ---------------------------------------------------------------------
        current_sub_count = Subscription.objects.count()
        needed_subs = max(0, target_subs_count - current_sub_count)
        self.stdout.write(f"7. Generating {needed_subs} subscriptions (to achieve {target_subs_count} total)...")

        if needed_subs > 0:
            # Distribution: 70% Active, 7% Draft, 9% Quotation, 6% Confirmed, 5% Paused, 3% Closed
            statuses = (
                [Subscription.Status.ACTIVE] * 70 +
                [Subscription.Status.DRAFT] * 7 +
                [Subscription.Status.QUOTATION] * 9 +
                [Subscription.Status.CONFIRMED] * 6 +
                [Subscription.Status.PAUSED] * 5 +
                [Subscription.Status.CLOSED] * 3
            )
            payment_terms_choices = [
                Subscription.PaymentTerms.IMMEDIATE,
                Subscription.PaymentTerms.DAYS_15,
                Subscription.PaymentTerms.DAYS_30,
            ]

            subs_to_create = []
            base_sub_id = current_sub_count + 1

            for i in range(needed_subs):
                sub_num = f"SUB-{(base_sub_id + i):05d}"
                status = random.choice(statuses)
                cust = customers[i % len(customers)]
                plan = plans[i % len(plans)]
                terms = random.choice(payment_terms_choices)

                # Spread start dates realistically according to billing period
                if plan.billing_period == Plan.BillingPeriod.DAILY:
                    days_ago = random.randint(1, 4)
                elif plan.billing_period == Plan.BillingPeriod.WEEKLY:
                    days_ago = random.randint(7, 28)
                elif plan.billing_period == Plan.BillingPeriod.YEARLY:
                    days_ago = random.randint(30, 365)
                else:  # Monthly
                    days_ago = random.randint(15, 90)

                start_d = today - timedelta(days=days_ago)

                # Expiry dates for closed or auto-closing
                expiry_d = None
                if status == Subscription.Status.CLOSED:
                    expiry_d = start_d + timedelta(days=random.randint(30, 90))
                elif plan.auto_close_on_end:
                    expiry_d = start_d + timedelta(days=random.randint(60, 180))

                subs_to_create.append(Subscription(
                    subscription_number=sub_num,
                    customer=cust,
                    plan=plan,
                    start_date=start_d,
                    expiry_date=expiry_d,
                    payment_terms=terms,
                    status=status,
                    created_by=admin_user,
                ))

            self.stdout.write("   Bulk saving subscriptions to database...")
            Subscription.objects.bulk_create(subs_to_create, batch_size=1000)

            # Query the newly created subscriptions to create lines
            all_created_subs = list(
                Subscription.objects.filter(subscription_number__gte=f"SUB-{base_sub_id:05d}")
                .select_related('plan')
                .order_by('id')
            )

            self.stdout.write("   Generating itemized subscription lines...")
            lines_to_create = []
            for sub in all_created_subs:
                # 1-3 lines per subscription
                num_lines = random.randint(1, 3)
                chosen_products = random.sample(products, num_lines)
                for prod in chosen_products:
                    qty = random.randint(1, 5)
                    # 50% chance of an attribute variant
                    chosen_var = None
                    matching_vars = [v for v in variants if v.product_id == prod.id]
                    if matching_vars and random.random() > 0.5:
                        chosen_var = random.choice(matching_vars)
                        unit_p = prod.sales_price + chosen_var.extra_price
                    else:
                        unit_p = prod.sales_price

                    chosen_tax = random.choice(taxes)
                    lines_to_create.append(SubscriptionLine(
                        subscription=sub,
                        product=prod,
                        variant=chosen_var,
                        quantity=qty,
                        unit_price=unit_p,
                        tax=chosen_tax,
                    ))

            SubscriptionLine.objects.bulk_create(lines_to_create, batch_size=2000)
            self.stdout.write(f"   Created {len(lines_to_create)} subscription lines.")

        total_subs = Subscription.objects.count()
        self.stdout.write(self.style.SUCCESS(f"   Total Subscriptions in DB: {total_subs}"))

        # ---------------------------------------------------------------------
        # 8. Invoices and Payments (~1,500 Invoices, Mixed Paid/Overdue)
        # ---------------------------------------------------------------------
        self.stdout.write("8. Generating past invoices and payment transactions...")
        current_inv_count = Invoice.objects.count()
        if current_inv_count < 1000:
            active_and_confirmed_subs = list(
                Subscription.objects.filter(status__in=[Subscription.Status.ACTIVE, Subscription.Status.CONFIRMED])
                .select_related('customer', 'plan')
                .prefetch_related('lines__tax')[:1500]
            )

            invs_to_create = []
            inv_lines_to_create = []
            payments_to_create = []

            base_inv_id = current_inv_count + 1
            inv_counter = 0

            # Define past periods: 6 months trailing
            for sub in active_and_confirmed_subs:
                inv_counter += 1
                inv_num = f"INV-{(base_inv_id + inv_counter):05d}"

                # Past billing period
                period_offset = random.randint(1, 5) * 30
                p_start = today - timedelta(days=period_offset + 30)
                p_end = p_start + timedelta(days=29)
                issue_d = p_start
                due_d = issue_d + timedelta(days=15)

                # Determine status: 60% Paid, 20% Overdue (unpaid past due), 15% Confirmed, 5% Draft
                roll = random.random()
                if roll < 0.60:
                    status = Invoice.Status.PAID
                elif roll < 0.80:
                    status = Invoice.Status.CONFIRMED  # Overdue if due_d < today
                elif roll < 0.95:
                    status = Invoice.Status.CONFIRMED
                    due_d = today + timedelta(days=15)  # Outstanding, not overdue
                else:
                    status = Invoice.Status.DRAFT

                # Calculate lines subtotal & taxes
                sub_lines = list(sub.lines.all())
                subtotal = sum((l.quantity * l.unit_price for l in sub_lines), Decimal('0.00')) or sub.plan.price
                tax_total = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'))
                discount_total = (subtotal * Decimal('0.05')).quantize(Decimal('0.01')) if random.random() > 0.7 else Decimal('0.00')
                grand_total = (subtotal - discount_total + tax_total).quantize(Decimal('0.01'))

                sent_at = timezone.now() - timedelta(days=random.randint(1, 30)) if status != Invoice.Status.DRAFT else None

                inv = Invoice(
                    invoice_number=inv_num,
                    subscription=sub,
                    customer=sub.customer,
                    period_start=p_start,
                    period_end=p_end,
                    issue_date=issue_d,
                    due_date=due_d,
                    status=status,
                    subtotal=subtotal,
                    discount_total=discount_total,
                    tax_total=tax_total,
                    grand_total=grand_total,
                    sent_at=sent_at,
                )
                invs_to_create.append(inv)

            self.stdout.write(f"   Bulk inserting {len(invs_to_create)} historical invoices...")
            # Use ignore_conflicts in case of subscription-period collisions
            Invoice.objects.bulk_create(invs_to_create, batch_size=1000, ignore_conflicts=True)

            # Query saved invoices
            saved_invoices = list(Invoice.objects.select_related('customer', 'subscription').order_by('id'))

            methods = [
                Payment.Method.BANK_TRANSFER,
                Payment.Method.CARD,
                Payment.Method.UPI,
                Payment.Method.CHEQUE,
                Payment.Method.CASH,
            ]

            for inv in saved_invoices:
                if inv.status == Invoice.Status.PAID:
                    # Full payment
                    pay_date = inv.issue_date + timedelta(days=random.randint(1, 10))
                    payments_to_create.append(Payment(
                        invoice=inv,
                        amount=inv.grand_total,
                        date=pay_date,
                        method=random.choice(methods),
                        note=f"Full settlement for {inv.invoice_number}",
                        recorded_by=random.choice(staff_users),
                    ))
                elif inv.status == Invoice.Status.CONFIRMED and random.random() > 0.5:
                    # Partial payment (leaves balance overdue/due)
                    partial_amt = (inv.grand_total * Decimal('0.4')).quantize(Decimal('0.01'))
                    if partial_amt > Decimal('0.00'):
                        payments_to_create.append(Payment(
                            invoice=inv,
                            amount=partial_amt,
                            date=inv.issue_date + timedelta(days=5),
                            method=random.choice(methods),
                            note=f"Partial installment for {inv.invoice_number}",
                            recorded_by=random.choice(staff_users),
                        ))

            Payment.objects.bulk_create(payments_to_create, batch_size=1000)
            self.stdout.write(f"   Created {len(payments_to_create)} payments.")

        # ---------------------------------------------------------------------
        # 9. Automated Catch-Up Billing Run for current active subscriptions
        # ---------------------------------------------------------------------
        self.stdout.write("9. Executing idempotent automated billing engine catch-up...")
        billing_results = generate_invoices(as_of_date=today)
        self.stdout.write(
            f"   Billing run output: {billing_results['created']} created, "
            f"{billing_results['skipped']} up-to-date, {billing_results['failed']} failed."
        )

        elapsed = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(
            f"\n=== Demo Seed Completed Successfully in {elapsed:.2f}s ===\n"
            f"Customers: {User.objects.filter(role=User.Role.CUSTOMER).count()}\n"
            f"Subscriptions: {Subscription.objects.count()} (Active: {Subscription.objects.filter(status='active').count()})\n"
            f"Invoices: {Invoice.objects.count()} (Overdue: {Invoice.objects.filter(due_date__lt=today).exclude(status__in=['paid','cancelled']).count()})\n"
            f"Payments: {Payment.objects.count()}\n"
        ))
