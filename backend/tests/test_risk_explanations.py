from app.services.risk_explanations import describe_signal, public_label


def test_graph_explanation_does_not_publish_other_account_ids():
    signal = {"code": "short_cycle", "label": "SYN-PRIVATE", "evidence": {
        "path": ["ACCOUNT-PRIVATE-A", "ACCOUNT-PRIVATE-B"], "window_hours": 1,
    }}
    text = describe_signal(signal)
    assert "цепочку" in text
    assert "PRIVATE" not in text
    assert "PRIVATE" not in public_label(signal["code"])


def test_explanation_uses_actual_numeric_evidence():
    text = describe_signal({"code": "rapid_cashout", "evidence": {
        "inbound_amount": 12345.67, "window_minutes": 10, "private_id": "OTHER-CLIENT",
    }})
    assert "12 345.67 ₸" in text
    assert "10 минут" in text
    assert "OTHER-CLIENT" not in text


def test_unknown_signal_never_publishes_arbitrary_evidence():
    text = describe_signal({"code": "unknown", "evidence": {"private": "SECRET"}})
    assert "SECRET" not in text
    assert "проверки" in text
