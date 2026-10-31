from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from accounts.models import User
from .models import Product, ProductVariant, Tax, Plan
from .forms import ProductForm, ProductVariantForm, TaxForm, PlanForm

class ProductAndVariantModelTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="BrightSuite Enterprise",
            product_type=Product.ProductType.SOFTWARE,
            sales_price=Decimal('1000.00'),
            cost_price=Decimal('400.00'),
            notes="Enterprise level subscription package"
        )

    def test_product_price_non_negative_validation(self):
        # Negative sales price
        product_invalid = Product(
            name="Negative Product",
            sales_price=Decimal('-10.00'),
            cost_price=Decimal('5.00')
        )
        with self.assertRaises(ValidationError):
            product_invalid.full_clean()

        # Negative cost price
        product_invalid_cost = Product(
            name="Negative Cost",
            sales_price=Decimal('10.00'),
            cost_price=Decimal('-5.00')
        )
        with self.assertRaises(ValidationError):
            product_invalid_cost.full_clean()

    def test_variant_price_calculation(self):
        """
        Verify specification scenario:
        Base Product sales price = 1000.00
        Brand=Odoo adds 560.00
        Final price = 1560.00
        """
        variant = ProductVariant.objects.create(
            product=self.product,
            attribute="Brand",
            value="Odoo",
            extra_price=Decimal('560.00')
        )
        self.assertEqual(variant.extra_price, Decimal('560.00'))
        self.assertEqual(variant.final_price, Decimal('1560.00'))

    def test_variant_with_zero_extra_price(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            attribute="Tier",
            value="Standard",
            extra_price=Decimal('0.00')
        )
        self.assertEqual(variant.final_price, Decimal('1000.00'))

    def test_variant_extra_price_cannot_be_negative(self):
        variant = ProductVariant(
            product=self.product,
            attribute="DiscountTier",
            value="LossLeader",
            extra_price=Decimal('-50.00')
        )
        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_unique_product_attribute_value_constraint(self):
        ProductVariant.objects.create(
            product=self.product,
            attribute="Brand",
            value="Odoo",
            extra_price=Decimal('560.00')
        )
        with self.assertRaises(Exception): # IntegrityError
            ProductVariant.objects.create(
                product=self.product,
                attribute="Brand",
                value="Odoo",
                extra_price=Decimal('300.00')
            )


class TaxModelTests(TestCase):
    def test_tax_calculation_percentage_and_fixed(self):
        tax = Tax.objects.create(
            name="VAT 18% + Regulatory Fee",
            percentage=Decimal('18.00'),
            fixed_amount=Decimal('5.00')
        )
        # On $100.00: 18% = 18.00 + 5.00 = 23.00
        calculated = tax.calculate_tax(Decimal('100.00'))
        self.assertEqual(calculated, Decimal('23.00'))

    def test_tax_percentage_cannot_be_negative(self):
        tax = Tax(name="Invalid Tax", percentage=Decimal('-5.00'))
        with self.assertRaises(ValidationError):
            tax.full_clean()


class PlanModelTests(TestCase):
    def test_plan_minimum_quantity_must_be_at_least_one(self):
        plan = Plan(
            name="Invalid Plan",
            price=Decimal('50.00'),
            minimum_quantity=0
        )
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_plan_date_validation(self):
        today = date.today()
        yesterday = today - timedelta(days=1)
        plan = Plan(
            name="Date Invalid Plan",
            price=Decimal('50.00'),
            available_from=today,
            available_to=yesterday
        )
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_valid_plan_with_flags(self):
        plan = Plan.objects.create(
            name="Enterprise Yearly",
            price=Decimal('1200.00'),
            billing_period=Plan.BillingPeriod.YEARLY,
            minimum_quantity=5,
            auto_close_on_end=True,
            closable_early=False,
            pausable=True,
            renewable=True
        )
        self.assertEqual(plan.minimum_quantity, 5)
        self.assertTrue(plan.auto_close_on_end)
        self.assertFalse(plan.closable_early)


class CatalogPermissionAndRoleAccessTests(TestCase):
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
        self.product = Product.objects.create(
            name="BrightSuite Standard",
            sales_price=Decimal('49.99'),
            cost_price=Decimal('15.00')
        )
        self.plan = Plan.objects.create(
            name="Standard Monthly",
            price=Decimal('49.99'),
            billing_period=Plan.BillingPeriod.MONTHLY
        )
        self.tax = Tax.objects.create(
            name="Standard Tax",
            percentage=Decimal('10.00')
        )

    def test_unauthenticated_user_redirected(self):
        response = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_customer_forbidden_from_catalog(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse('catalog:plan_list'))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse('catalog:tax_list'))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_catalog(self):
        self.client.force_login(self.staff)
        # Can view products
        response = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BrightSuite Standard")

        # Can view product detail
        response = self.client.get(reverse('catalog:product_detail', kwargs={'pk': self.product.pk}))
        self.assertEqual(response.status_code, 200)

        # Can view plans
        response = self.client.get(reverse('catalog:plan_list'))
        self.assertEqual(response.status_code, 200)

        # Can view taxes
        response = self.client.get(reverse('catalog:tax_list'))
        self.assertEqual(response.status_code, 200)

    def test_staff_cannot_create_or_edit_or_delete_products(self):
        self.client.force_login(self.staff)
        # Attempt to create product -> 403
        response = self.client.get(reverse('catalog:product_create'))
        self.assertEqual(response.status_code, 403)

        # Attempt to edit product -> 403
        response = self.client.get(reverse('catalog:product_edit', kwargs={'pk': self.product.pk}))
        self.assertEqual(response.status_code, 403)

        # Attempt to delete product -> 403
        response = self.client.post(reverse('catalog:product_delete', kwargs={'pk': self.product.pk}))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_and_edit_product(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('catalog:product_create'), {
            'name': 'BrightCloud Storage',
            'product_type': 'software',
            'sales_price': '29.99',
            'cost_price': '10.00',
            'notes': 'Cloud storage 1TB',
            'is_active': True,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product.objects.filter(name='BrightCloud Storage').exists())

    def test_admin_can_create_variant(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('catalog:variant_create', kwargs={'product_pk': self.product.pk}),
            {
                'attribute': 'Brand',
                'value': 'Odoo',
                'extra_price': '560.00',
                'is_active': True,
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        variant = ProductVariant.objects.get(product=self.product, value='Odoo')
        self.assertEqual(variant.final_price, Decimal('609.99')) # 49.99 + 560.00

    def test_catalog_search_and_filter(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('catalog:product_list') + '?q=Standard')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BrightSuite Standard")

        response = self.client.get(reverse('catalog:product_list') + '?q=NonExistent')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Products Found")
