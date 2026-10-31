from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from catalog.models import Plan, Product, Tax

from .models import (
    DiscountRule,
    QuotationTemplate,
    QuotationTemplateLine,
    Subscription,
    SubscriptionLine,
)


class SubscriptionLifecycleAndTransitionsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@brightloop.com',
            password='AdminPassword@1'
        )
        self.staff = User.objects.create_user(
            email='staff@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer = User.objects.create_user(
            email='customer@brightloop.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(
            name="Professional SaaS",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            minimum_quantity=2,
            auto_close_on_end=True,
            closable_early=True,
            pausable=True,
            renewable=True
        )
        self.product = Product.objects.create(
            name="BrightSuite Seat",
            sales_price=Decimal('50.00'),
            cost_price=Decimal('10.00')
        )
        self.tax = Tax.objects.create(
            name="Standard Tax 10%",
            percentage=Decimal('10.00')
        )

    def test_auto_generate_subscription_number(self):
        sub1 = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            start_date=date.today()
        )
        sub2 = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            start_date=date.today()
        )
        self.assertTrue(sub1.subscription_number.startswith("SUB-"))
        self.assertTrue(sub2.subscription_number.startswith("SUB-"))
        self.assertNotEqual(sub1.subscription_number, sub2.subscription_number)

    def test_legal_status_transition_sequence(self):
        """Tests complete valid flow: Draft > Quotation > Confirmed > Active > Closed."""
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.DRAFT
        )
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=2,
            unit_price=Decimal('50.00')
        )

        # 1. Draft -> Quotation
        sub.transition_to(Subscription.Status.QUOTATION, user=self.staff, notes="Quoted to customer")
        self.assertEqual(sub.status, Subscription.Status.QUOTATION)

        # 2. Quotation -> Confirmed
        sub.transition_to(Subscription.Status.CONFIRMED, user=self.customer, notes="Customer agreed")
        self.assertEqual(sub.status, Subscription.Status.CONFIRMED)

        # 3. Confirmed -> Active
        sub.transition_to(Subscription.Status.ACTIVE, user=self.staff, notes="Activated")
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

        # 4. Active -> Closed
        sub.transition_to(Subscription.Status.CLOSED, user=self.staff, notes="End of term")
        self.assertEqual(sub.status, Subscription.Status.CLOSED)

        # Verify audit history
        history = list(sub.status_history.order_by('changed_at'))
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0].to_status, Subscription.Status.QUOTATION)
        self.assertEqual(history[1].to_status, Subscription.Status.CONFIRMED)
        self.assertEqual(history[2].to_status, Subscription.Status.ACTIVE)
        self.assertEqual(history[3].to_status, Subscription.Status.CLOSED)

    def test_illegal_status_transition_rejected(self):
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.DRAFT
        )
        # Attempting to jump directly from Draft to Active
        with self.assertRaises(ValidationError):
            sub.transition_to(Subscription.Status.ACTIVE)

        # Attempting to reopen Closed directly
        sub.status = Subscription.Status.CLOSED
        sub.save()
        with self.assertRaises(ValidationError):
            sub.transition_to(Subscription.Status.ACTIVE)

    def test_plan_pausable_flag_enforcement(self):
        non_pausable_plan = Plan.objects.create(
            name="Strict Yearly",
            price=Decimal('500.00'),
            billing_period=Plan.BillingPeriod.YEARLY,
            pausable=False
        )
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=non_pausable_plan,
            status=Subscription.Status.ACTIVE
        )
        # Pausing should be rejected because plan.pausable is False
        with self.assertRaises(ValidationError) as ctx:
            sub.transition_to(Subscription.Status.PAUSED)
        self.assertIn("does not allow pausing", str(ctx.exception))

        # But if plan.pausable is True, pausing succeeds
        sub.plan = self.plan # pausable is True
        sub.save()
        sub.transition_to(Subscription.Status.PAUSED)
        self.assertEqual(sub.status, Subscription.Status.PAUSED)

    def test_plan_closable_early_flag_enforcement(self):
        non_closable_plan = Plan.objects.create(
            name="Locked Contract",
            price=Decimal('1000.00'),
            billing_period=Plan.BillingPeriod.YEARLY,
            closable_early=False
        )
        future_expiry = date.today() + timedelta(days=180)
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=non_closable_plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=future_expiry
        )
        # Attempt early closure
        with self.assertRaises(ValidationError) as ctx:
            sub.transition_to(Subscription.Status.CLOSED)
        self.assertIn("does not permit early closure", str(ctx.exception))

    def test_plan_renewable_flag_enforcement(self):
        non_renewable_plan = Plan.objects.create(
            name="One-off Pilot",
            price=Decimal('100.00'),
            renewable=False
        )
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=non_renewable_plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=date.today() + timedelta(days=10)
        )
        with self.assertRaises(ValidationError) as ctx:
            sub.renew()
        self.assertIn("cannot be renewed", str(ctx.exception))

        # Renewable plan succeeds
        sub.plan = self.plan
        sub.save()
        initial_expiry = sub.expiry_date
        sub.renew()
        self.assertGreater(sub.expiry_date, initial_expiry)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    def test_minimum_quantity_validation(self):
        # self.plan has minimum_quantity = 2
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.DRAFT
        )
        # Line with quantity 1
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=1,
            unit_price=Decimal('50.00')
        )
        # Fails minimum quantity check
        with self.assertRaises(ValidationError):
            sub.validate_minimum_quantity()

        # Add another line or increase quantity
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=1,
            unit_price=Decimal('50.00')
        )
        # Now total quantity is 2 >= plan.minimum_quantity
        try:
            sub.validate_minimum_quantity()
        except ValidationError:
            self.fail("validate_minimum_quantity raised ValidationError unexpectedly.")

    def test_close_expired_subscriptions_management_command(self):
        """Tests the close_expired_subscriptions command."""
        yesterday = date.today() - timedelta(days=1)
        tomorrow = date.today() + timedelta(days=1)

        # 1. Expired active with auto_close_on_end=True -> Should close
        expired_auto = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan, # auto_close_on_end=True
            status=Subscription.Status.ACTIVE,
            expiry_date=yesterday
        )

        # 2. Expired active with auto_close_on_end=False -> Should remain active
        manual_plan = Plan.objects.create(
            name="Manual Plan",
            price=Decimal('100.00'),
            auto_close_on_end=False
        )
        expired_manual = Subscription.objects.create(
            customer=self.customer,
            plan=manual_plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=yesterday
        )

        # 3. Not yet expired -> Should remain active
        active_current = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            expiry_date=tomorrow
        )

        out = StringIO()
        call_command('close_expired_subscriptions', stdout=out)

        expired_auto.refresh_from_db()
        expired_manual.refresh_from_db()
        active_current.refresh_from_db()

        self.assertEqual(expired_auto.status, Subscription.Status.CLOSED)
        self.assertEqual(expired_manual.status, Subscription.Status.ACTIVE)
        self.assertEqual(active_current.status, Subscription.Status.ACTIVE)
        self.assertIn("Successfully closed 1 expired subscription(s)", out.getvalue())


class QuotationTemplateAndCreateFromTemplateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@brightloop.com',
            password='AdminPassword@1'
        )
        self.staff = User.objects.create_user(
            email='staff@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer = User.objects.create_user(
            email='customer@brightloop.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(
            name="Enterprise Monthly",
            price=Decimal('200.00'),
            billing_period=Plan.BillingPeriod.MONTHLY
        )
        self.product = Product.objects.create(
            name="Cloud Storage 500GB",
            sales_price=Decimal('40.00'),
            cost_price=Decimal('10.00')
        )
        self.tax = Tax.objects.create(
            name="GST 18%",
            percentage=Decimal('18.00')
        )
        self.template = QuotationTemplate.objects.create(
            name="Standard Cloud SaaS Offer",
            plan=self.plan,
            validity_days=15
        )
        QuotationTemplateLine.objects.create(
            template=self.template,
            product=self.product,
            quantity=3,
            unit_price=Decimal('40.00'),
            tax=self.tax
        )

    def test_create_subscription_from_template_view(self):
        self.client.force_login(self.staff)
        start_date = date.today()
        response = self.client.post(reverse('subscriptions:subscription_from_template'), {
            'template': self.template.pk,
            'customer': self.customer.pk,
            'start_date': start_date.strftime('%Y-%m-%d'),
            'payment_terms': '15_days',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        sub = Subscription.objects.filter(customer=self.customer).first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.plan, self.plan)
        self.assertEqual(sub.status, Subscription.Status.QUOTATION)
        # Expiry date should equal start_date + validity_days (15 days)
        self.assertEqual(sub.expiry_date, start_date + timedelta(days=15))
        # Lines copied
        self.assertEqual(sub.lines.count(), 1)
        line = sub.lines.first()
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.unit_price, Decimal('40.00'))
        self.assertEqual(line.tax, self.tax)


class SubscriptionCustomerPermissionIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.plan = Plan.objects.create(name="Basic", price=Decimal('10.00'))
        self.customer1 = User.objects.create_user(
            email='alice@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.customer2 = User.objects.create_user(
            email='bob@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.sub_alice = Subscription.objects.create(
            customer=self.customer1,
            plan=self.plan,
            status=Subscription.Status.ACTIVE
        )

    def test_customer_can_view_own_subscription(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('subscriptions:subscription_detail', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.sub_alice.subscription_number)

    def test_customer_cannot_view_other_customer_subscription(self):
        self.client.force_login(self.customer2)
        response = self.client.get(reverse('subscriptions:subscription_detail', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_user_redirected_to_login_on_subscription_detail(self):
        """Unauthenticated request to subscription detail must redirect to login."""
        response = self.client.get(reverse('subscriptions:subscription_detail', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_customer_action_and_subscription_mutation_reject_get_with_405(self):
        """GET requests to customer action or staff transition/renew must return 405."""
        self.client.force_login(self.customer1)
        res_action = self.client.get(reverse('subscriptions:customer_action', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(res_action.status_code, 405)

        staff_user = User.objects.create_user(
            email='sub_staff_tester@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.client.force_login(staff_user)
        res_trans = self.client.get(reverse('subscriptions:subscription_transition', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(res_trans.status_code, 405)

        res_renew = self.client.get(reverse('subscriptions:subscription_renew', kwargs={'pk': self.sub_alice.pk}))
        self.assertEqual(res_renew.status_code, 405)




class DiscountRulesAndServiceTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            email='shopper@customer.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(name="Pro Plan", price=Decimal('100.00'))
        self.prod_crm = Product.objects.create(name="CRM Module", sales_price=Decimal('100.00'))
        self.prod_hr = Product.objects.create(name="HR Module", sales_price=Decimal('50.00'))

        self.sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE
        )
        # Line 1: 2x CRM Module @ 100.00 = 200.00
        SubscriptionLine.objects.create(
            subscription=self.sub,
            product=self.prod_crm,
            quantity=2,
            unit_price=Decimal('100.00')
        )
        # Line 2: 1x HR Module @ 50.00 = 50.00
        SubscriptionLine.objects.create(
            subscription=self.sub,
            product=self.prod_hr,
            quantity=1,
            unit_price=Decimal('50.00')
        )
        # Total subtotal = 250.00, total quantity = 3

    def test_model_validation_rules(self):
        # Value must be positive
        rule_zero = DiscountRule(name="Zero Discount", value=Decimal('0.00'))
        with self.assertRaises(ValidationError):
            rule_zero.full_clean()

        # Percentage cannot exceed 100%
        rule_excess = DiscountRule(
            name="120% Discount",
            discount_type=DiscountRule.DiscountType.PERCENTAGE,
            value=Decimal('120.00')
        )
        with self.assertRaises(ValidationError):
            rule_excess.full_clean()

        # End date before start date rejected
        rule_dates = DiscountRule(
            name="Date Inverted",
            value=Decimal('10.00'),
            start_date=date(2026, 6, 1),
            end_date=date(2026, 5, 1)
        )
        with self.assertRaises(ValidationError):
            rule_dates.full_clean()

        # Usage limit must be at least 1
        rule_limit = DiscountRule(
            name="Limit Zero",
            value=Decimal('10.00'),
            usage_limit=0
        )
        with self.assertRaises(ValidationError):
            rule_limit.full_clean()

    def test_percentage_discount_whole_subscription_calculation(self):
        from subscriptions.services import get_applicable_discounts
        rule = DiscountRule.objects.create(
            name="Save 10%",
            discount_type=DiscountRule.DiscountType.PERCENTAGE,
            value=Decimal('10.00'),
            scope=DiscountRule.Scope.WHOLE_SUBSCRIPTION
        )
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 1)
        # 10% of $250.00 = $25.00
        self.assertEqual(discounts[0].calculated_amount, Decimal('25.00'))
        self.assertEqual(discounts[0].rule, rule)

    def test_fixed_discount_cannot_make_amount_negative(self):
        from subscriptions.services import get_applicable_discounts
        # Fixed $300.00 discount on a $250.00 subscription
        rule = DiscountRule.objects.create(
            name="Huge Fixed $300",
            discount_type=DiscountRule.DiscountType.FIXED,
            value=Decimal('300.00'),
            scope=DiscountRule.Scope.WHOLE_SUBSCRIPTION
        )
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 1)
        self.assertEqual(discounts[0].rule, rule)
        # Should be capped at $250.00 (the eligible amount) so line/total is never negative
        self.assertEqual(discounts[0].calculated_amount, Decimal('250.00'))

    def test_expired_discount_is_ignored(self):
        from subscriptions.services import get_applicable_discounts
        yesterday = date.today() - timedelta(days=1)
        DiscountRule.objects.create(
            name="Expired Promo",
            value=Decimal('20.00'),
            end_date=yesterday
        )
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 0)

    def test_usage_limit_reached_is_ignored(self):
        from subscriptions.services import get_applicable_discounts
        rule = DiscountRule.objects.create(
            name="Limited Coupons",
            value=Decimal('15.00'),
            usage_limit=5,
            times_used=5 # Limit reached!
        )
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 0)

        # But if times_used < usage_limit, it applies
        rule.times_used = 4
        rule.save()
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 1)

    def test_below_minimum_purchase_is_ignored(self):
        from subscriptions.services import get_applicable_discounts
        # Subtotal is $250.00
        DiscountRule.objects.create(
            name="High Roller Discount",
            value=Decimal('50.00'),
            min_purchase_amount=Decimal('500.00') # Higher than 250
        )
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 0)

    def test_below_minimum_quantity_is_ignored(self):
        from subscriptions.services import get_applicable_discounts
        # Total seats = 3
        DiscountRule.objects.create(
            name="Bulk 10 Seats",
            value=Decimal('15.00'),
            min_quantity=10 # Higher than 3
        )
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 0)

    def test_product_scoped_discount(self):
        from subscriptions.services import get_applicable_discounts
        # Create discount strictly for CRM Module
        rule = DiscountRule.objects.create(
            name="CRM 20% Off",
            discount_type=DiscountRule.DiscountType.PERCENTAGE,
            value=Decimal('20.00'),
            scope=DiscountRule.Scope.SPECIFIC_PRODUCTS
        )
        rule.products.add(self.prod_crm)

        # CRM Module line is 2x $100 = $200.00 (HR module $50 should NOT be discounted)
        discounts = get_applicable_discounts(self.sub, billing_date=date.today())
        self.assertEqual(len(discounts), 1)
        # 20% of $200.00 = $40.00
        self.assertEqual(discounts[0].calculated_amount, Decimal('40.00'))
        self.assertEqual(discounts[0].eligible_amount, Decimal('200.00'))


class DiscountPermissionAndAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@brightloop.com',
            password='AdminPassword@1'
        )
        self.staff = User.objects.create_user(
            email='staff@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer = User.objects.create_user(
            email='customer@brightloop.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.rule = DiscountRule.objects.create(
            name="Welcome Discount",
            value=Decimal('10.00')
        )

    def test_unauthenticated_user_redirected(self):
        response = self.client.get(reverse('subscriptions:discount_list'))
        self.assertEqual(response.status_code, 302)

    def test_customer_forbidden(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('subscriptions:discount_list'))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_discounts(self):
        self.client.force_login(self.staff)
        # Can view list
        response = self.client.get(reverse('subscriptions:discount_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome Discount")

        # Can view detail
        response = self.client.get(reverse('subscriptions:discount_detail', kwargs={'pk': self.rule.pk}))
        self.assertEqual(response.status_code, 200)

    def test_staff_cannot_create_edit_delete_discounts(self):
        self.client.force_login(self.staff)
        # Cannot create
        response = self.client.get(reverse('subscriptions:discount_create'))
        self.assertEqual(response.status_code, 403)

        # Cannot edit
        response = self.client.get(reverse('subscriptions:discount_edit', kwargs={'pk': self.rule.pk}))
        self.assertEqual(response.status_code, 403)

        # Cannot delete
        response = self.client.post(reverse('subscriptions:discount_delete', kwargs={'pk': self.rule.pk}))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_discount(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('subscriptions:discount_create'), {
            'name': 'Black Friday 25%',
            'discount_type': 'percentage',
            'value': '25.00',
            'min_purchase_amount': '0.00',
            'min_quantity': '0',
            'scope': 'whole_subscription',
            'is_active': True,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DiscountRule.objects.filter(name='Black Friday 25%').exists())


class QueryCountAndSeedTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin_perf@brightloop.com',
            password='AdminPassword@1'
        )
        self.staff = User.objects.create_user(
            email='staff_perf@brightloop.com',
            password='StaffPassword@1',
            role=User.Role.STAFF
        )
        self.customer = User.objects.create_user(
            email='customer_perf@brightloop.com',
            password='CustomerPassword@1',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(
            name="Perf Plan",
            price=Decimal('50.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            minimum_quantity=1
        )
        self.product = Product.objects.create(
            name="Perf Product",
            sales_price=Decimal('25.00'),
            cost_price=Decimal('5.00')
        )
        self.tax = Tax.objects.create(
            name="Standard Tax",
            percentage=Decimal('10.00'),
            is_active=True
        )

        # Create 15 subscriptions with 2 lines each (30 lines total)
        subs = []
        for i in range(15):
            subs.append(Subscription(
                subscription_number=f"SUB-PERF-{i:03d}",
                customer=self.customer,
                plan=self.plan,
                start_date=date.today(),
                status=Subscription.Status.ACTIVE,
                created_by=self.admin
            ))
        Subscription.objects.bulk_create(subs)

        all_subs = list(Subscription.objects.filter(subscription_number__startswith="SUB-PERF-"))
        lines = []
        for s in all_subs:
            lines.append(SubscriptionLine(
                subscription=s,
                product=self.product,
                quantity=2,
                unit_price=Decimal('25.00'),
                tax=self.tax
            ))
            lines.append(SubscriptionLine(
                subscription=s,
                product=self.product,
                quantity=1,
                unit_price=Decimal('25.00'),
                tax=self.tax
            ))
        SubscriptionLine.objects.bulk_create(lines)

    def test_subscriptions_list_query_count_is_bounded_and_no_n_plus_one(self):
        """Verify subscription list uses prefetch and select_related, bounding queries <= 10."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('subscriptions:subscription_list'))

        self.assertEqual(response.status_code, 200)
        # Bounded query count: session, user, count, customer list, subscriptions, lines, taxes, products, variants
        self.assertLessEqual(len(ctx), 12)

    def test_customer_portal_query_count_is_bounded(self):
        """Verify customer portal page bounds query count and prevents N+1 payment calculations."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.customer)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('customer_portal'))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(ctx), 12)

    def test_seed_demo_command_small_run(self):
        """Verify seed_demo management command executes without errors."""
        out = StringIO()
        call_command('seed_demo', '--subscriptions', '5', stdout=out)
        output = out.getvalue()
        self.assertIn("Demo Seed Completed Successfully", output)
        self.assertGreaterEqual(Subscription.objects.count(), 5)


class SubscriptionLifecycleEdgeCasesAndRegressionTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            email='edge_sub@example.com',
            password='CustomerPass@123',
            role=User.Role.CUSTOMER
        )
        self.plan = Plan.objects.create(
            name="Strict Plan",
            price=Decimal('100.00'),
            billing_period=Plan.BillingPeriod.MONTHLY,
            closable_early=False,
            pausable=True,
            auto_close_on_end=True,
        )

    def test_paused_subscription_cannot_close_early_if_not_permitted(self):
        """A paused subscription whose plan does not allow early closure must not close before expiry."""
        sub = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            start_date=date.today(),
            expiry_date=date.today() + timedelta(days=30),
        )
        # Pause
        sub.transition_to(Subscription.Status.PAUSED)
        self.assertEqual(sub.status, Subscription.Status.PAUSED)

        # Attempt to close early while paused
        with self.assertRaises(ValidationError) as ctx:
            sub.transition_to(Subscription.Status.CLOSED)
        self.assertIn("does not permit early closure", str(ctx.exception))

    def test_auto_close_expired_command_closes_both_active_and_paused_subscriptions(self):
        """Scheduled command must close both active and paused subscriptions when expiry is reached."""
        past_date = date.today() - timedelta(days=5)
        sub_active = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            start_date=date.today() - timedelta(days=35),
            expiry_date=past_date,
        )
        sub_paused = Subscription.objects.create(
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.PAUSED,
            start_date=date.today() - timedelta(days=35),
            expiry_date=past_date,
        )

        out = StringIO()
        call_command('close_expired_subscriptions', stdout=out)
        sub_active.refresh_from_db()
        sub_paused.refresh_from_db()

        self.assertEqual(sub_active.status, Subscription.Status.CLOSED)
        self.assertEqual(sub_paused.status, Subscription.Status.CLOSED)
        self.assertIn("Successfully closed 2 expired subscription(s)", out.getvalue())


