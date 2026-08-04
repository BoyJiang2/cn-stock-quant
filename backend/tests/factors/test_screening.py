from app.factors.screening import screen_factor_metrics
from app.research_cemetery import factor_cemetery_reason


def test_factor_screening_rejects_short_or_non_positive_research_evidence():
    status, reasons = screen_factor_metrics(
        n_dates=40,
        rankic_mean=-0.01,
        rankic_ir=-0.1,
        long_short_return=-0.01,
        long_short_turnover=0.2,
    )

    assert status == "rejected"
    assert len(reasons) == 4


def test_factor_screening_marks_partial_evidence_for_watch():
    status, reasons = screen_factor_metrics(
        n_dates=100,
        rankic_mean=0.01,
        rankic_ir=0.2,
        long_short_return=0.01,
        long_short_turnover=0.2,
    )

    assert status == "watch"
    assert any("120" in reason for reason in reasons)
    assert factor_cemetery_reason(
        {
            "screening_status": status,
            "screening_reasons": reasons,
            "n_dates": 100,
            "rankic_mean": 0.01,
        }
    ).startswith("watch:")


def test_factor_screening_requires_strong_and_tradable_candidate_evidence():
    status, reasons = screen_factor_metrics(
        n_dates=160,
        rankic_mean=0.03,
        rankic_ir=0.25,
        long_short_return=0.01,
        long_short_turnover=0.5,
    )

    assert status == "candidate"
    assert "rolling OOS" in reasons[0]
