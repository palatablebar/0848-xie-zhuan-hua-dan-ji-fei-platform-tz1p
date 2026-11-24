from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.decision import decide_rows
from app.domain.prepare import prepare_rows
from app.intake.service import load

CLEAN = [{'call_id': 'C1',
  'subscriber': 'A',
  'start_at': '2026-11-20 10:00:00',
  'end_at': '2026-11-20 10:01:20',
  'charged_seconds': '120',
  'rate_per_min_cents': '20',
  'direction': 'OUT'},
 {'call_id': 'C2',
  'subscriber': 'A',
  'start_at': '2026-11-20 10:01:00',
  'end_at': '2026-11-20 10:02:10',
  'charged_seconds': '60',
  'rate_per_min_cents': '20',
  'direction': 'OUT'},
 {'call_id': 'C3',
  'subscriber': 'B',
  'start_at': '2026-11-20 23:59:30',
  'end_at': '2026-11-21 00:00:45',
  'charged_seconds': '120',
  'rate_per_min_cents': '30',
  'direction': 'IN'},
 {'call_id': 'C4',
  'subscriber': 'C',
  'start_at': '2026-11-20 10:00:00',
  'end_at': '2026-11-20 10:01:00',
  'charged_seconds': '60',
  'rate_per_min_cents': '10',
  'direction': 'OUT'},
 {'call_id': 'C5',
  'subscriber': 'C',
  'start_at': '2026-11-20 10:01:00',
  'end_at': '2026-11-20 10:02:00',
  'charged_seconds': '60',
  'rate_per_min_cents': '10',
  'direction': 'OUT'}]


def row(**changes):
    item = dict(CLEAN[0])
    item.update(changes)
    return item


def calc(rows):
    items, issues = prepare_rows(rows)
    return calculate_rows(items), issues


def run(rows):
    items, issues = prepare_rows(rows)
    return decide_rows(calculate_rows(items)), issues


def run_profile(profile):
    return run(load(profile)["rows"])
