import pytest

from app.services.durable_execution import checkpoint_compatible


@pytest.mark.parametrize('old_rule', [
    'time-windows-v3-hotel-nights', 'time-windows-v4-meal-candidates',
    'time-windows-v5-hotel-departure', 'time-windows-v6-conflict-classification',
    'time-windows-v7-whole-day-budget', 'time-windows-v8-fact-completion',
    'time-windows-v9-route-evidence', 'time-windows-v10-adjustment-resume',
])
@pytest.mark.parametrize('stage,expected', [
    ('draft', False), ('day_draft:0', False), ('attractions:digest', False),
    ('schedule', False), ('schedule_day:0:digest', False), ('routes', True),
    ('hotels:digest', True), ('restaurants:digest', True), ('weather:digest', True),
])
def test_changed_candidate_policy_recomputes_choices_but_preserves_unchanged_provider_facts(stage, expected, old_rule):
    old = {'rules': old_rule, 'model': 'fixture', 'prompt': 'same', 'schema': 'planning-v1'}
    new = {**old, 'rules': 'time-windows-v11-independent-visits', 'generation': 'day-candidates-v2-independent-window'}
    assert checkpoint_compatible(stage, old, new) is expected
    assert not checkpoint_compatible(stage, old, {**new, 'model': 'changed'})
    assert checkpoint_compatible(stage, old, {**new, 'prompt': 'changed'}) is expected
    assert not checkpoint_compatible(stage, old, {**new, 'generation': 'unrecognized-policy'})
    assert not checkpoint_compatible(stage, {**old, 'rules': 'unrecognized-rules'}, new)
    assert not checkpoint_compatible(stage, new, {**new, 'prompt': 'changed'})
