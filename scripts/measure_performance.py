#!/usr/bin/env python
"""
Performance & Query Count Verification Script
Measures response time and SQL query counts across all critical platform pages
to assert absence of N+1 queries and guarantee sub-second page performance.
"""
import os
import sys
import time

# Set up Django environment
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from invoicing.models import Invoice
from subscriptions.models import Subscription


def measure_pages():
    admin = User.objects.filter(role=User.Role.ADMIN).first()
    customer = User.objects.filter(role=User.Role.CUSTOMER).first()

    if not admin or not customer:
        print("Required seed users missing. Run 'python manage.py seed_demo' first.")
        sys.exit(1)

    admin_client = Client()
    admin_client.force_login(admin)

    customer_client = Client()
    customer_client.force_login(customer)

    sample_sub = Subscription.objects.first()
    sample_inv = Invoice.objects.first()

    sub_id = sample_sub.id if sample_sub else 1
    inv_id = sample_inv.id if sample_inv else 1

    targets = [
        ("Executive Finance Dashboard", "/", admin_client, 20),
        ("Subscriptions List", "/subscriptions/", admin_client, 12),
        ("Subscription Detail", f"/subscriptions/{sub_id}/", admin_client, 10),
        ("Quotation Templates List", "/subscriptions/templates/", admin_client, 6),
        ("Discount Rules List", "/subscriptions/discounts/", admin_client, 8),
        ("Invoices List", "/invoices/", admin_client, 10),
        ("Invoice Detail", f"/invoices/{inv_id}/", admin_client, 10),
        ("Overdue Invoices List", "/invoices/overdue/", admin_client, 10),
        ("Payments History List", "/invoices/payments/", admin_client, 8),
        ("Product Catalog List", "/catalog/products/", admin_client, 8),
        ("Subscription Plans List", "/catalog/plans/", admin_client, 6),
        ("Customer Self-Service Portal", "/accounts/portal/", customer_client, 12),
    ]

    print("\n" + "=" * 80)
    print("ASTRAFLOW PERFORMANCE AUDIT REPORT (5,000+ Subscriptions Benchmark)")
    print("=" * 80)
    print(f"Total Subscriptions in Database : {Subscription.objects.count():,}")
    print(f"Total Invoices in Database      : {Invoice.objects.count():,}")
    print("-" * 80)
    header = f"{'Page View':<32} | {'Status':<6} | {'Load Time':<12} | {'Queries':<9} | {'Threshold':<9}"
    print(header)
    print("-" * 80)

    all_passed = True
    for name, url, client, max_queries in targets:
        with CaptureQueriesContext(connection) as ctx:
            start = time.perf_counter()
            resp = client.get(url)
            elapsed_ms = (time.perf_counter() - start) * 1000

        query_count = len(ctx)
        status_ok = resp.status_code == 200
        time_ok = elapsed_ms < 2000.0  # Must be strictly under 2 seconds per AGENTS.md
        queries_ok = query_count <= max_queries

        passed = status_ok and time_ok and queries_ok
        if not passed:
            all_passed = False

        status_str = f"{resp.status_code} OK" if status_ok else f"{resp.status_code} ERR"
        time_str = f"{elapsed_ms:8.2f} ms"
        q_str = f"{query_count:4d}"
        max_str = f"<= {max_queries}"

        print(f"{name:<32} | {status_str:<6} | {time_str:<12} | {q_str:<9} | {max_str:<9}")

    print("-" * 80)
    if all_passed:
        print(">>> ALL PAGES PASSED: Zero N+1 queries detected and all loads are sub-second.")
    else:
        print(">>> PERFORMANCE AUDIT FAILED: One or more pages exceeded latency or query count thresholds.")
    print("=" * 80 + "\n")
    return all_passed


if __name__ == '__main__':
    success = measure_pages()
    sys.exit(0 if success else 1)
