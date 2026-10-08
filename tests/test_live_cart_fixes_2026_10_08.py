"""Regressions from the 8 Oct 2026 live cart test (bill exactness, checkout link, labels)."""
from smartplate.integrations import swiggy_live


def test_taxes_and_charges_is_read_exactly_with_swiggys_label():
    # The live cart: Swiggy's documented `taxes_and_charges` (21.58) was not read; the bill
    # showed a computed ₹22 "Other charges (as Swiggy shows them)" against the rounded total.
    cart = {"pricing": {"item_total": 160, "delivery_charge": 6, "taxes_and_charges": 21.58, "to_pay": 188}}
    bill = swiggy_live.bill_breakdown(cart, 160)
    assert bill["lines"] == [{"label": "Item Total", "amount": 160.0}, {"label": "Delivery Fee", "amount": 6.0},
                             {"label": "GST & Other Charges", "amount": 21.58}]
    assert bill["to_pay"] == 188.0 and bill["itemised"]
    assert bill["rounding"] == 0.42                         # Swiggy rounds the total; shown as a note, not a line
    assert not any("as Swiggy shows" in l["label"] for l in bill["lines"])


def test_paise_payload_keeps_paise():
    cart = {"pricing": {"item_total": 16000, "delivery_charge": 600, "taxes_and_charges": 2158,
                        "to_pay_in_paise": 18758}}
    bill = swiggy_live.bill_breakdown(cart, 160)
    assert [l["amount"] for l in bill["lines"]] == [160.0, 6.0, 21.58] and bill["to_pay"] == 187.58
    assert "rounding" not in bill and "unitemised" not in bill


def test_applied_coupon_from_offers_and_suggested_coupon_ignored():
    applied = swiggy_live.bill_breakdown({"pricing": {"item_total": 200, "taxes_and_charges": 10, "to_pay": 160},
                                          "offers": {"coupon_applied": "SAVE50", "coupon_discount": 50}}, 200)
    assert applied["lines"][-1] == {"label": "Discount", "amount": -50.0} and applied["itemised"]
    suggested = swiggy_live.bill_breakdown({"pricing": {"item_total": 200, "taxes_and_charges": 10, "to_pay": 210},
                                            "offers": {"coupon_applied": "SAVE50", "coupon_discount": 0}}, 200)
    assert all(l["label"] != "Discount" for l in suggested["lines"])


def test_checkout_link_is_swiggys_checkout_page():
    assert swiggy_live.CHECKOUT_URL == "https://www.swiggy.com/checkout"


def test_cancellation_note_only_when_swiggy_sends_it():
    reply = {"structuredContent": {"data": {"data": {"items": []}, "message":
             "Note: 100% cancellation fee if cancelled after 60 seconds of placing the order."}}}
    assert swiggy_live.cancellation_note(reply) == \
        "Note: 100% cancellation fee if cancelled after 60 seconds of placing the order."
    as_text = {"content": [{"type": "text", "text": '{"data": {"note": "100% cancellation fee if cancelled after 60 seconds"}}'}]}
    assert swiggy_live.cancellation_note(as_text) == "100% cancellation fee if cancelled after 60 seconds"
    assert swiggy_live.cancellation_note({"data": {"pricing": {"to_pay": 10}}}) is None
