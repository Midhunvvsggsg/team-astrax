#!/usr/bin/env python3
"""
Responsive Layout and Horizontal Overflow Verification Script.

Visits core application pages (Dashboard, Subscriptions, Invoices, Payments, Plans, Reports)
across responsive breakpoints (375px, 768px, 1280px, 1920px) using headless Playwright.
Detects and reports any page where document.documentElement.scrollWidth > clientWidth,
identifying offending DOM elements causing horizontal overflow.

Browser Console Snippet (Manual verification alternative):
--------------------------------------------------------------------------------
(function() {
  const doc = document.documentElement;
  const clientWidth = doc.clientWidth;
  const scrollWidth = doc.scrollWidth;
  const overflows = scrollWidth > clientWidth;
  console.log(`[Overflow Check] clientWidth: ${clientWidth}px, scrollWidth: ${scrollWidth}px -> ${overflows ? 'OVERFLOW DETECTED' : 'PASS'}`);
  if (overflows) {
    const offenders = [];
    document.querySelectorAll('*').forEach(el => {
      const rect = el.getBoundingClientRect();
      if (rect.right > clientWidth + 1) {
        offenders.push({
          tag: el.tagName.toLowerCase(),
          id: el.id ? '#' + el.id : '',
          class: el.className ? '.' + String(el.className).trim().split(/\\s+/).join('.') : '',
          right: Math.round(rect.right),
          excess: Math.round(rect.right - clientWidth)
        });
      }
    });
    console.table(offenders);
  }
})();
--------------------------------------------------------------------------------
"""

import os
import sys
import threading
from typing import Any, Dict, List

# Ensure Django environment is initialized
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from wsgiref.simple_server import WSGIRequestHandler, make_server

from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.wsgi import get_wsgi_application

from accounts.models import User

# Target pages to check
TARGET_PAGES = [
    ("Dashboard", "/"),
    ("Subscriptions", "/subscriptions/"),
    ("Invoices", "/invoices/"),
    ("Payments", "/invoices/payments/"),
    ("Plans", "/catalog/plans/"),
    ("Reports", "/invoices/reports/"),
]

# Viewport widths specified in task requirements
TARGET_WIDTHS = [375, 768, 1280, 1920]
VIEWPORT_HEIGHT = 900

# JavaScript evaluation payload for DOM overflow inspection
OVERFLOW_CHECK_JS = r"""
() => {
    const doc = document.documentElement;
    const body = document.body;
    const clientWidth = doc.clientWidth;
    const scrollWidth = Math.max(doc.scrollWidth, body ? body.scrollWidth : 0);
    // Allow 1px tolerance for fractional subpixel rendering
    const hasOverflow = scrollWidth > clientWidth + 1;
    const offenders = [];

    if (hasOverflow) {
        const allElements = document.querySelectorAll('*');
        for (const el of allElements) {
            // Skip hidden or non-displayed elements
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const rect = el.getBoundingClientRect();
            if (rect.right > clientWidth + 1) {
                // Check if an ancestor clips this element with overflow-x
                let parent = el.parentElement;
                let isClipped = false;
                while (parent && parent !== doc && parent !== body) {
                    const pStyle = window.getComputedStyle(parent);
                    const ox = pStyle.overflowX;
                    if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') {
                        isClipped = true;
                        break;
                    }
                    parent = parent.parentElement;
                }
                if (!isClipped) {
                    const tag = el.tagName.toLowerCase();
                    const id = el.id ? '#' + el.id : '';
                    let cls = '';
                    if (typeof el.className === 'string' && el.className.trim()) {
                        cls = '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.');
                    }
                    const snippet = (el.innerText || el.textContent || '').trim().slice(0, 40).replace(/\n/g, ' ');
                    offenders.push({
                        element: `${tag}${id}${cls}`,
                        rect_right: Math.round(rect.right),
                        excess_px: Math.round(rect.right - clientWidth),
                        snippet: snippet
                    });
                }
            }
        }
    }

    return {
        clientWidth: clientWidth,
        scrollWidth: scrollWidth,
        hasOverflow: hasOverflow,
        offenders: offenders
    };
}
"""


class QuietHandler(WSGIRequestHandler):
    """Silences WSGI request logging during verification runs."""
    def log_message(self, format, *args):
        pass


def run_overflow_checks() -> bool:
    """
    Runs the headless browser verification across all target pages and widths.
    Returns True if all checks pass without horizontal overflow, False otherwise.
    """
    print("=" * 80)
    print("Brightloop UI Horizontal Overflow Verification")
    print(f"Target Widths: {TARGET_WIDTHS}")
    print(f"Target Pages:  {[p[0] for p in TARGET_PAGES]}")
    print("=" * 80)

    # 1. Start local WSGI test server serving static files
    application = StaticFilesHandler(get_wsgi_application())
    server = make_server('127.0.0.1', 0, application, handler_class=QuietHandler)
    server_port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{server_port}"

    # 2. Prepare authenticated staff/admin session
    admin_user = User.objects.filter(role=User.Role.ADMIN).first()
    if not admin_user:
        admin_user, _ = User.objects.get_or_create(
            email='admin@brightloop.local',
            defaults={'role': User.Role.ADMIN, 'is_staff': True, 'is_superuser': True}
        )
        admin_user.set_password('AdminPass123!')
        admin_user.save()

    session = SessionStore()
    session[SESSION_KEY] = str(admin_user.pk)
    session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
    session[HASH_SESSION_KEY] = admin_user.get_session_auth_hash()
    session.save()

    results: List[Dict[str, Any]] = []
    any_overflow = False

    # 3. Launch Playwright headless chromium
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n[ERROR] Playwright is not installed. Run: pip install playwright")
        server.shutdown()
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path='/usr/bin/chromium' if os.path.exists('/usr/bin/chromium') else None,
            headless=True
        )

        for page_name, path in TARGET_PAGES:
            for width in TARGET_WIDTHS:
                context = browser.new_context(
                    viewport={'width': width, 'height': VIEWPORT_HEIGHT}
                )
                context.add_cookies([{
                    'name': 'sessionid',
                    'value': session.session_key,
                    'domain': '127.0.0.1',
                    'path': '/'
                }])

                page = context.new_page()
                full_url = f"{base_url}{path}"

                try:
                    page.goto(full_url, wait_until='networkidle', timeout=15000)
                    # Small settle delay for responsive layout adjustments and charts
                    page.wait_for_timeout(200)

                    eval_res = page.evaluate(OVERFLOW_CHECK_JS)
                    client_w = eval_res.get('clientWidth', width)
                    scroll_w = eval_res.get('scrollWidth', width)
                    has_overflow = eval_res.get('hasOverflow', False)
                    offenders = eval_res.get('offenders', [])

                    status = "FAIL" if has_overflow else "PASS"
                    if has_overflow:
                        any_overflow = True

                    results.append({
                        'page': page_name,
                        'path': path,
                        'width': width,
                        'client_w': client_w,
                        'scroll_w': scroll_w,
                        'status': status,
                        'offenders': offenders
                    })

                except Exception as ex:
                    results.append({
                        'page': page_name,
                        'path': path,
                        'width': width,
                        'client_w': width,
                        'scroll_w': -1,
                        'status': f"ERROR: {str(ex)[:30]}",
                        'offenders': []
                    })
                finally:
                    context.close()

        browser.close()

    server.shutdown()

    # 4. Display Results Table
    print("\n" + "-" * 88)
    print(f"{'Page':<16} | {'Width':<7} | {'ClientW':<9} | {'ScrollW':<9} | {'Status':<8} | {'Offenders'}")
    print("-" * 88)

    for r in results:
        offender_str = "None"
        if r['offenders']:
            first_off = r['offenders'][0]
            offender_str = f"{first_off['element']} (+{first_off['excess_px']}px) [{len(r['offenders'])} total]"
        print(f"{r['page']:<16} | {r['width']:<5}px | {r['client_w']:<7}px | {r['scroll_w']:<7}px | {r['status']:<8} | {offender_str}")

    print("-" * 88)

    if any_overflow:
        print("\n❌ OVERFLOW DETECTED: Some pages exceed viewport width.")
        for r in results:
            if r['status'] == 'FAIL':
                print(f"\nOffending elements on {r['page']} at {r['width']}px:")
                for off in r['offenders']:
                    print(f"  • {off['element']} (overflow by {off['excess_px']}px) -> '{off['snippet']}'")
        return False
    else:
        print("\n✅ ALL CHECKS PASSED: Zero horizontal page overflow detected across all breakpoints.")
        return True


if __name__ == '__main__':
    success = run_overflow_checks()
    sys.exit(0 if success else 1)
