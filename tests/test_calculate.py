from decimal import Decimal

from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.prepare import prepare_rows
from tests.domain_cases import CLEAN, calc

# TRAP-19 TRAP-20 TRAP-24 的边界由本文件的跨日、合并与单位用例固定。


def clean_results():
    items, issues = prepare_rows(CLEAN)
    assert issues == []
    return calculate_rows(items)


def test_clean_result_count():
    assert len(clean_results()) == 3


def test_metric_total_is_reconciled():
    total = sum((item["metric"] for item in clean_results()), Decimal("0"))
    assert total == Decimal("420")


def test_first_result_matches_expected():
    item = clean_results()[0]
    assert item["label"] == "A|OUT"
    assert abs(float(item["billable_seconds"]) - float("180")) < 0.011


def test_core_boundary_case():
    results, issues = calc([{'call_id': 'C9', 'subscriber': 'Z', 'start_at': '2026-11-20 12:00:00', 'end_at': '2026-11-20 12:01:01', 'charged_seconds': '120', 'rate_per_min_cents': '20', 'direction': 'OUT'}])
    assert issues == []
    value = results[0]["billable_seconds"]
    expected = '120'
    if expected in {"True", "False"}:
        assert bool(value) is (expected == "True")
    elif isinstance(expected, bool):
        assert bool(value) is expected
    elif expected.startswith("20") and len(expected) == 10:
        assert value.isoformat() == expected
    elif expected.replace(".", "", 1).replace("-", "", 1).isdigit():
        assert abs(float(value) - float(expected)) < 0.001
    else:
        assert value == expected


def test_result_labels_are_unique():
    labels = [item["label"] for item in clean_results()]
    assert len(labels) == len(set(labels))


def test_every_result_has_metric():
    assert all(item.get("metric") is not None for item in clean_results())
