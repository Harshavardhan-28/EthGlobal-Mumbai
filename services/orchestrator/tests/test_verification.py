import unittest
from datetime import date

from app import verification as v

NOW = 1_790_000_000  # 2026-09-21
TODAY = "2026-09-21"
SELLER = "27AABCN1234K1ZL"
BUYER = "29AAECR5678M1ZI"
COMPANY = {"id": "northstar", "type": "COMPANY", "kycStatus": "VERIFIED"}
FREELANCER = {"id": "priya", "type": "SOLE_PROPRIETOR", "kycStatus": "VERIFIED"}


def invoice(**overrides):
    base = {
        "country": "IN",
        "sellerTaxId": SELLER,
        "buyerTaxId": BUYER,
        "buyerType": "BUSINESS",
        "documentType": "INV",
        "invoiceNumber": "INV-2048",
        "invoiceDate": TODAY,
        "dueDate": "2026-10-21",
        "faceValueMinor": 10_000_000_000,
        "irn": v.compute_irn(SELLER, "2026-27", "INV", "INV-2048"),
    }
    return {**base, **overrides}


class GstinTests(unittest.TestCase):
    def test_check_digit_matches_published_worked_example(self) -> None:
        # Worked example from GSTN's check-digit algorithm write-up: 27AAPFU0939F1Z -> V.
        self.assertEqual(v.gstin_check_digit("27AAPFU0939F1Z"), "V")
        self.assertIsNone(v.gstin_error("27AAPFU0939F1ZV"))

    def test_rejects_bad_format_state_and_check_digit(self) -> None:
        self.assertEqual(v.gstin_error("27AAPFU0939F1ZA"), "GSTIN_CHECKSUM_INVALID")
        self.assertEqual(v.gstin_error("27AAPFU0939F1"), "GSTIN_FORMAT_INVALID")
        self.assertEqual(v.gstin_error("not a gstin"), "GSTIN_FORMAT_INVALID")
        bad_state = "40AAPFU0939F1Z"
        self.assertEqual(v.gstin_error(bad_state + v.gstin_check_digit(bad_state)), "GSTIN_STATE_INVALID")

    def test_lowercase_and_spaces_are_normalised(self) -> None:
        self.assertIsNone(v.gstin_error(f"  {SELLER.lower()} "))

    def test_pan_identifies_the_legal_entity(self) -> None:
        self.assertEqual(v.pan_of(SELLER), "AABCN1234K")

    def test_financial_year_runs_april_to_march(self) -> None:
        self.assertEqual(v.financial_year(date(2026, 3, 31)), "2025-26")
        self.assertEqual(v.financial_year(date(2026, 4, 1)), "2026-27")


class VerifyInvoiceTests(unittest.TestCase):
    def test_gst_e_invoice_with_matching_irn_is_top_tier(self) -> None:
        result = v.verify_invoice(invoice(), COMPANY, NOW)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["tier"], "GST_E_INVOICE")
        self.assertEqual(result["advanceCapBps"], 8000)
        self.assertTrue(all(c["ok"] for c in result["checks"]))

    def test_missing_or_mismatched_irn_downgrades_instead_of_rejecting(self) -> None:
        for irn in (None, "ab" * 32, "short"):
            result = v.verify_invoice(invoice(irn=irn), COMPANY, NOW)
            self.assertTrue(result["eligible"], irn)
            self.assertEqual(result["tier"], "GSTIN_VERIFIED", irn)
            self.assertEqual(result["advanceCapBps"], 5000)

    def test_irn_for_a_different_invoice_number_does_not_match(self) -> None:
        other = v.compute_irn(SELLER, "2026-27", "INV", "INV-2049")
        self.assertEqual(v.verify_invoice(invoice(irn=other), COMPANY, NOW)["tier"], "GSTIN_VERIFIED")

    def test_invalid_gstins_are_rejected(self) -> None:
        self.assertEqual(v.verify_invoice(invoice(buyerTaxId="29AAECR5678M1ZA"), COMPANY, NOW)["rejection"], "BUYER_GSTIN_INVALID")
        self.assertEqual(v.verify_invoice(invoice(sellerTaxId="27AABCN1234K1ZA"), COMPANY, NOW)["rejection"], "SELLER_GSTIN_INVALID")

    def test_same_pan_on_both_sides_is_a_self_invoice(self) -> None:
        branch = "29AABCN1234K1Z"  # Northstar's own Karnataka registration
        result = v.verify_invoice(invoice(buyerTaxId=branch + v.gstin_check_digit(branch)), COMPANY, NOW)
        self.assertEqual(result["rejection"], "SELF_INVOICE")

    def test_freelancer_without_gstin_is_allowed_at_the_lowest_tier(self) -> None:
        result = v.verify_invoice(invoice(sellerTaxId=None, irn=None), FREELANCER, NOW)
        self.assertEqual(result["tier"], "DECLARED")
        self.assertEqual(result["advanceCapBps"], 3000)
        self.assertEqual(v.verify_invoice(invoice(sellerTaxId=None, irn=None), COMPANY, NOW)["rejection"], "SELLER_GSTIN_REQUIRED")

    def test_foreign_invoice_uses_tax_ids_and_invoice_number(self) -> None:
        foreign = invoice(country="GB", sellerTaxId="GB123456789", buyerTaxId="GB987654321", irn=None)
        result = v.verify_invoice(foreign, COMPANY, NOW)
        self.assertEqual(result["tier"], "FOREIGN_INVOICE")
        self.assertEqual(result["advanceCapBps"], 5000)
        self.assertEqual(v.verify_invoice({**foreign, "buyerTaxId": ""}, COMPANY, NOW)["rejection"], "CONSUMER_INVOICE_NOT_SUPPORTED")
        self.assertEqual(v.verify_invoice({**foreign, "sellerTaxId": ""}, COMPANY, NOW)["rejection"], "FOREIGN_ID_REQUIRED")
        self.assertEqual(v.verify_invoice({**foreign, "buyerTaxId": "GB123456789"}, COMPANY, NOW)["rejection"], "SELF_INVOICE")


class EligibilityTests(unittest.TestCase):
    def test_personal_borrowers_are_not_eligible(self) -> None:
        self.assertEqual(v.verify_invoice(invoice(), {"id": "p", "type": "INDIVIDUAL"}, NOW)["rejection"], "BORROWER_TYPE_INELIGIBLE")

    def test_consumer_invoices_are_not_financed(self) -> None:
        self.assertEqual(v.verify_invoice(invoice(buyerType="CONSUMER"), COMPANY, NOW)["rejection"], "CONSUMER_INVOICE_NOT_SUPPORTED")

    def test_credit_notes_are_not_financed(self) -> None:
        self.assertEqual(v.verify_invoice(invoice(documentType="CRN"), COMPANY, NOW)["rejection"], "DOCUMENT_TYPE_INELIGIBLE")

    def test_due_date_must_be_future_and_within_120_days(self) -> None:
        self.assertEqual(v.verify_invoice(invoice(dueDate=TODAY), COMPANY, NOW)["rejection"], "INVOICE_TERM_INELIGIBLE")
        self.assertEqual(v.verify_invoice(invoice(dueDate="2027-02-01"), COMPANY, NOW)["rejection"], "INVOICE_TERM_INELIGIBLE")
        self.assertTrue(v.verify_invoice(invoice(dueDate="2027-01-19"), COMPANY, NOW)["eligible"])

    def test_amount_and_invoice_number_are_required(self) -> None:
        self.assertEqual(v.verify_invoice(invoice(faceValueMinor=0), COMPANY, NOW)["rejection"], "INVOICE_AMOUNT_INVALID")
        self.assertEqual(v.verify_invoice(invoice(invoiceNumber=" "), COMPANY, NOW)["rejection"], "INVOICE_NUMBER_REQUIRED")


if __name__ == "__main__":
    unittest.main()
