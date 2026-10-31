import csv
import difflib
import hashlib
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from automation.clock import get_today
from invoicing.models import BankStatement, BankStatementRow, Invoice, Payment

# Maximum allowed file size for bank statement CSVs: 5 MB
MAX_CSV_FILE_SIZE = 5 * 1024 * 1024


def compute_row_hash(date_val: Any, amount_val: Decimal, reference: str, description: str) -> str:
    """
    Computes a deterministic SHA-256 hash for a statement row to guarantee idempotency.
    Normalizes date, amount, reference, and description so re-uploading identical rows
    will produce identical hashes.
    """
    norm_date = str(date_val).strip()
    norm_amount = f"{Decimal(str(amount_val)).quantize(Decimal('0.01'))}"
    norm_ref = (reference or "").strip().lower()
    norm_desc = (description or "").strip().lower()
    payload = f"{norm_date}|{norm_amount}|{norm_ref}|{norm_desc}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def parse_statement_csv(csv_file) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parses an uploaded bank statement CSV file, returning:
      (valid_rows, error_messages)
    Validates required columns (date, description, amount, reference),
    sanitizes types, and checks constraints.
    """
    errors = []
    parsed_rows = []

    # File size validation
    if hasattr(csv_file, 'size') and csv_file.size > MAX_CSV_FILE_SIZE:
        errors.append(f"File size exceeds the 5MB limit ({round(csv_file.size / (1024*1024), 2)}MB).")
        return [], errors

    try:
        content = csv_file.read()
        if isinstance(content, bytes):
            # Handle BOM and decode
            try:
                decoded_text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                decoded_text = content.decode('latin-1')
        else:
            decoded_text = content
    except Exception as e:
        errors.append(f"Failed to read file: {str(e)}")
        return [], errors

    if not decoded_text.strip():
        errors.append("The uploaded CSV file is empty.")
        return [], errors

    reader = csv.reader(io.StringIO(decoded_text))
    try:
        raw_headers = next(reader)
    except StopIteration:
        errors.append("CSV file does not contain a header row.")
        return [], errors

    # Normalize header mapping
    header_map = {}
    for idx, col in enumerate(raw_headers):
        clean_name = col.strip().lower().replace(' ', '_')
        header_map[clean_name] = idx

    required_fields = ['date', 'description', 'amount', 'reference']
    missing_fields = [f for f in required_fields if f not in header_map]
    if missing_fields:
        errors.append(f"Missing required CSV column(s): {', '.join(missing_fields)}. Found columns: {', '.join(raw_headers)}.")
        return [], errors

    date_formats = [
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%m/%d/%Y',
        '%Y/%m/%d',
        '%d-%m-%Y',
        '%Y%m%d',
    ]

    for line_num, row_data in enumerate(reader, start=2):
        if not row_data or not any(cell.strip() for cell in row_data):
            continue  # skip blank lines

        # Extract values
        try:
            raw_date = row_data[header_map['date']].strip()
            raw_desc = row_data[header_map['description']].strip()
            raw_amount = row_data[header_map['amount']].strip()
            raw_ref = row_data[header_map['reference']].strip()
        except IndexError:
            errors.append(f"Line {line_num}: Incomplete row data (expected {len(required_fields)} columns).")
            continue

        # Parse date
        parsed_date = None
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue

        if not parsed_date:
            errors.append(f"Line {line_num}: Invalid date format '{raw_date}'. Expected YYYY-MM-DD or DD/MM/YYYY.")
            continue

        # Parse amount
        clean_amt_str = raw_amount.replace('$', '').replace(',', '').strip()
        try:
            amt = Decimal(clean_amt_str).quantize(Decimal('0.01'))
            if amt <= Decimal('0.00'):
                errors.append(f"Line {line_num}: Amount must be positive (${amt}).")
                continue
        except (InvalidOperation, ValueError):
            errors.append(f"Line {line_num}: Invalid numeric amount '{raw_amount}'.")
            continue

        parsed_rows.append({
            'row_number': line_num - 1,
            'date': parsed_date,
            'description': raw_desc,
            'amount': amt,
            'reference': raw_ref,
        })

    if not parsed_rows and not errors:
        errors.append("No valid transaction rows found in CSV.")

    return parsed_rows, errors


def compute_customer_similarity(text: str, customer: User) -> Tuple[float, str]:
    """
    Computes a rule-based offline similarity score (0.0 to 1.0) between the row text
    (reference + description) and customer profile information.
    """
    if not text or not customer:
        return 0.0, ""

    text_lower = text.lower()
    full_name = (customer.full_name or "").strip()
    first_name = (customer.first_name or "").strip()
    last_name = (customer.last_name or "").strip()
    email_name = customer.email.split('@')[0] if customer.email else ""

    best_score = 0.0
    best_reason = ""

    # Exact full name contained in text
    if full_name and len(full_name) >= 3 and full_name.lower() in text_lower:
        return 1.0, f"Full name exact match: '{full_name}'"

    # Both first and last name present
    if first_name and last_name and first_name.lower() in text_lower and last_name.lower() in text_lower:
        return 0.95, f"First and last name match: '{first_name} {last_name}'"

    # Email username contained
    if email_name and len(email_name) >= 3 and email_name.lower() in text_lower:
        if 0.90 > best_score:
            best_score = 0.90
            best_reason = f"Email handle match: '{email_name}'"

    # Last name match
    if last_name and len(last_name) >= 3 and last_name.lower() in text_lower:
        if 0.75 > best_score:
            best_score = 0.75
            best_reason = f"Last name match: '{last_name}'"

    # Token overlap and SequenceMatcher ratio
    text_words = [w.strip(".,;:()[]{}-_/\"'") for w in text_lower.split() if len(w) >= 3]
    if full_name:
        for word in text_words:
            ratio = difflib.SequenceMatcher(None, word, full_name.lower()).ratio()
            if ratio > best_score:
                best_score = ratio
                best_reason = f"Fuzzy word similarity with '{full_name}' ({round(ratio*100)}%)"

    return best_score, best_reason


def extract_candidate_invoice_number(text: str) -> List[str]:
    """Extracts all invoice number patterns like 'INV-00001' from text."""
    if not text:
        return []
    matches = re.findall(r'(?i)\b(INV[-_]?\d{4,6})\b', text)
    # Normalize INV-00001
    normalized = []
    for m in matches:
        clean = m.upper().replace('_', '-')
        if clean not in normalized:
            normalized.append(clean)
    return normalized


def process_bank_statement(
    statement: BankStatement,
    parsed_rows: List[Dict[str, Any]],
    user: Optional[User] = None,
) -> BankStatement:
    """
    Executes the bank statement reconciliation pipeline row-by-row:
    1. Computes row hash.
    2. Checks for duplicates against previously applied rows.
    3. Rule-based invoice matching:
       - High confidence -> Auto-reconcile & record payment
       - Medium confidence -> Needs review (queued for one-click approval)
       - Low confidence -> Unmatched (listed for manual handling)
    4. Updates BankStatement summary metrics atomically.
    """
    # Fetch all confirmed unpaid invoices
    unpaid_invoices_qs = (
        Invoice.objects
        .filter(status=Invoice.Status.CONFIRMED)
        .select_related('customer')
        .prefetch_related('payments')
    )
    # Map invoice_number -> invoice
    invoice_map = {inv.invoice_number.upper(): inv for inv in unpaid_invoices_qs}
    # Dynamic remaining balance tracking in-memory to prevent over-allocation across rows
    remaining_balance_map = {inv.id: inv.amount_due for inv in unpaid_invoices_qs}

    auto_reconciled = 0
    needs_review = 0
    unmatched = 0
    duplicates = 0

    with transaction.atomic():
        for row_data in parsed_rows:
            r_num = row_data['row_number']
            r_date = row_data['date']
            r_desc = row_data['description']
            r_amount = row_data['amount']
            r_ref = row_data['reference']

            row_hash = compute_row_hash(r_date, r_amount, r_ref, r_desc)
            combined_text = f"{r_ref} {r_desc}".strip()

            # ------------------------------------------------------------------
            # 1. Idempotency Check: Was this exact row hash already applied?
            # ------------------------------------------------------------------
            previously_applied = BankStatementRow.objects.filter(
                row_hash=row_hash,
                payment__isnull=False,
            ).select_related('payment__invoice').first()

            if previously_applied:
                duplicates += 1
                BankStatementRow.objects.create(
                    statement=statement,
                    row_number=r_num,
                    date=r_date,
                    description=r_desc,
                    amount=r_amount,
                    reference=r_ref,
                    row_hash=row_hash,
                    confidence=BankStatementRow.Confidence.LOW,
                    status=BankStatementRow.Status.DUPLICATE,
                    match_reasons=(
                        f"Duplicate transaction ignored: Row hash matches previously reconciled "
                        f"Payment #{previously_applied.payment_id} for "
                        f"{previously_applied.payment.invoice.invoice_number}."
                    ),
                    matched_invoice=previously_applied.matched_invoice,
                    payment=previously_applied.payment,
                )
                continue

            # ------------------------------------------------------------------
            # 2. Candidate Matching
            # ------------------------------------------------------------------
            found_inv_nums = extract_candidate_invoice_number(combined_text)
            matched_inv = None
            for num in found_inv_nums:
                if num in invoice_map:
                    matched_inv = invoice_map[num]
                    break

            # Evaluate match confidence
            confidence = BankStatementRow.Confidence.LOW
            reasons = []

            if matched_inv:
                inv_due = remaining_balance_map.get(matched_inv.id, matched_inv.amount_due)
                cust_sim, sim_desc = compute_customer_similarity(combined_text, matched_inv.customer)

                if r_amount == inv_due:
                    confidence = BankStatementRow.Confidence.HIGH
                    reasons.append(f"Exact invoice number match ({matched_inv.invoice_number}) and exact balance match (${r_amount}).")
                    if cust_sim >= 0.5:
                        reasons.append(f"Customer confirmation: {sim_desc}.")
                elif Decimal('0.00') < r_amount < inv_due:
                    # Partial payment
                    if cust_sim >= 0.7:
                        # Invoice number + customer name match with partial payment -> High confidence
                        confidence = BankStatementRow.Confidence.HIGH
                        reasons.append(f"Invoice number matched ({matched_inv.invoice_number}) with customer confirmation ({sim_desc}). Partial payment of ${r_amount} against ${inv_due} balance.")
                    else:
                        # Invoice number only, partial amount without name confirmation -> Medium confidence
                        confidence = BankStatementRow.Confidence.MEDIUM
                        reasons.append(f"Invoice number matched ({matched_inv.invoice_number}); partial payment of ${r_amount} against ${inv_due} balance. Awaiting staff approval.")
                else:
                    # Amount exceeds due balance
                    confidence = BankStatementRow.Confidence.LOW
                    reasons.append(f"Invoice {matched_inv.invoice_number} found, but payment amount (${r_amount}) exceeds remaining balance (${inv_due}). Overpayment rejected.")
                    matched_inv = None
            else:
                # No invoice number in reference: search candidate invoices by customer name and exact amount
                amount_and_name_candidates = []
                for inv in unpaid_invoices_qs:
                    inv_due = remaining_balance_map.get(inv.id, inv.amount_due)
                    if inv_due == r_amount:
                        sim, sim_desc = compute_customer_similarity(combined_text, inv.customer)
                        if sim >= 0.5:
                            amount_and_name_candidates.append((inv, sim, sim_desc))

                if len(amount_and_name_candidates) == 1:
                    inv, sim, sim_desc = amount_and_name_candidates[0]
                    matched_inv = inv
                    if sim >= 0.85:
                        confidence = BankStatementRow.Confidence.HIGH
                        reasons.append(f"Exact amount match (${r_amount}) with strong customer confirmation: {sim_desc}. Unique unpaid invoice {inv.invoice_number}.")
                    else:
                        confidence = BankStatementRow.Confidence.MEDIUM
                        reasons.append(f"Exact amount match (${r_amount}) and customer match: {sim_desc}. Awaiting staff review.")
                elif len(amount_and_name_candidates) > 1:
                    # Ambiguous match: multiple invoices have identical amount and customer name
                    confidence = BankStatementRow.Confidence.MEDIUM
                    best_cand = max(amount_and_name_candidates, key=lambda x: x[1])
                    matched_inv = best_cand[0]
                    reasons.append(f"Ambiguous: {len(amount_and_name_candidates)} invoices match amount and customer. Suggested candidate: {matched_inv.invoice_number}.")
                else:
                    confidence = BankStatementRow.Confidence.LOW
                    reasons.append("No unpaid confirmed invoice matched invoice number, amount, or customer name.")

            # ------------------------------------------------------------------
            # 3. Action Dispatch
            # ------------------------------------------------------------------
            if confidence == BankStatementRow.Confidence.HIGH and matched_inv:
                # High confidence -> Auto-reconcile and create Payment
                payment_note = (
                    f"Auto-reconciled from bank statement '{statement.filename}' row #{r_num}. "
                    f"Ref: {r_ref or 'N/A'}"
                )
                payment = Payment.objects.create(
                    invoice=matched_inv,
                    amount=r_amount,
                    date=r_date,
                    method=Payment.Method.BANK_TRANSFER,
                    note=payment_note,
                    recorded_by=user,
                )
                # Update in-memory remaining balance
                remaining_balance_map[matched_inv.id] = max(Decimal('0.00'), remaining_balance_map[matched_inv.id] - r_amount)
                auto_reconciled += 1

                BankStatementRow.objects.create(
                    statement=statement,
                    row_number=r_num,
                    date=r_date,
                    description=r_desc,
                    amount=r_amount,
                    reference=r_ref,
                    row_hash=row_hash,
                    confidence=confidence,
                    status=BankStatementRow.Status.AUTO_RECONCILED,
                    match_reasons="; ".join(reasons),
                    matched_invoice=matched_inv,
                    payment=payment,
                    reviewed_by=user,
                    reviewed_at=timezone.now(),
                )

            elif confidence == BankStatementRow.Confidence.MEDIUM and matched_inv:
                # Medium confidence -> Goes to review screen for one-click approval
                needs_review += 1
                BankStatementRow.objects.create(
                    statement=statement,
                    row_number=r_num,
                    date=r_date,
                    description=r_desc,
                    amount=r_amount,
                    reference=r_ref,
                    row_hash=row_hash,
                    confidence=confidence,
                    status=BankStatementRow.Status.NEEDS_REVIEW,
                    match_reasons="; ".join(reasons),
                    matched_invoice=matched_inv,
                )

            else:
                # Low confidence / Unmatched -> Manual handling
                unmatched += 1
                BankStatementRow.objects.create(
                    statement=statement,
                    row_number=r_num,
                    date=r_date,
                    description=r_desc,
                    amount=r_amount,
                    reference=r_ref,
                    row_hash=row_hash,
                    confidence=BankStatementRow.Confidence.LOW,
                    status=BankStatementRow.Status.UNMATCHED,
                    match_reasons="; ".join(reasons),
                    matched_invoice=None,
                )

        # Update Statement Summary Counts
        statement.total_rows = len(parsed_rows)
        statement.auto_reconciled_count = auto_reconciled
        statement.needs_review_count = needs_review
        statement.unmatched_count = unmatched
        statement.duplicate_count = duplicates
        statement.save()

    return statement


def approve_reconciliation_row(row: BankStatementRow, user: User) -> Payment:
    """
    Executes one-click approval on a medium confidence row.
    Records payment and transitions row to MANUALLY_RECONCILED.
    """
    if row.status != BankStatementRow.Status.NEEDS_REVIEW:
        raise ValidationError(f"Row #{row.row_number} is not in review status (current: {row.get_status_display()}).")

    if not row.matched_invoice:
        raise ValidationError(f"Row #{row.row_number} has no candidate invoice assigned.")

    invoice = row.matched_invoice
    if invoice.status != Invoice.Status.CONFIRMED:
        raise ValidationError(f"Invoice {invoice.invoice_number} is no longer Confirmed (current: {invoice.get_status_display()}).")

    if row.amount > invoice.amount_due:
        raise ValidationError(f"Payment amount (${row.amount}) exceeds remaining balance (${invoice.amount_due}).")

    with transaction.atomic():
        payment_note = (
            f"Approved from bank statement '{row.statement.filename}' row #{row.row_number} by {user.email}. "
            f"Ref: {row.reference or 'N/A'}"
        )
        payment = Payment.objects.create(
            invoice=invoice,
            amount=row.amount,
            date=row.date,
            method=Payment.Method.BANK_TRANSFER,
            note=payment_note,
            recorded_by=user,
        )

        row.status = BankStatementRow.Status.MANUALLY_RECONCILED
        row.payment = payment
        row.reviewed_by = user
        row.reviewed_at = timezone.now()
        row.match_reasons += f" | Approved by {user.email} on {get_today()}."
        row.save()

        # Update statement counts
        statement = row.statement
        statement.needs_review_count = max(0, statement.needs_review_count - 1)
        statement.save()

    return payment


def manually_reconcile_row(row: BankStatementRow, invoice: Invoice, user: User) -> Payment:
    """
    Manually maps an unmatched row to a specific confirmed unpaid invoice and records payment.
    """
    if row.status not in [BankStatementRow.Status.UNMATCHED, BankStatementRow.Status.NEEDS_REVIEW]:
        raise ValidationError(f"Row #{row.row_number} has already been reconciled.")

    if invoice.status != Invoice.Status.CONFIRMED:
        raise ValidationError(f"Invoice {invoice.invoice_number} is not Confirmed.")

    if row.amount > invoice.amount_due:
        raise ValidationError(f"Payment amount (${row.amount}) exceeds remaining balance (${invoice.amount_due}).")

    with transaction.atomic():
        payment = Payment.objects.create(
            invoice=invoice,
            amount=row.amount,
            date=row.date,
            method=Payment.Method.BANK_TRANSFER,
            note=f"Manually reconciled from statement '{row.statement.filename}' row #{row.row_number} by {user.email}.",
            recorded_by=user,
        )

        old_status = row.status
        row.status = BankStatementRow.Status.MANUALLY_RECONCILED
        row.matched_invoice = invoice
        row.payment = payment
        row.reviewed_by = user
        row.reviewed_at = timezone.now()
        row.match_reasons += f" | Manually assigned to {invoice.invoice_number} by {user.email}."
        row.save()

        # Update statement counts
        statement = row.statement
        if old_status == BankStatementRow.Status.NEEDS_REVIEW:
            statement.needs_review_count = max(0, statement.needs_review_count - 1)
        elif old_status == BankStatementRow.Status.UNMATCHED:
            statement.unmatched_count = max(0, statement.unmatched_count - 1)
        statement.save()

    return payment
