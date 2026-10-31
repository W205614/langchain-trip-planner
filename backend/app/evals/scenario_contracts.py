"""Compare an excluded stale day without relaxing any itinerary content."""
import copy
import json

ALLOWED_REASONS = frozenset({'SCHEDULE_DEPENDENCIES_CHANGED', 'PLANNING_INPUT_CHANGED'})

def signature(value):
    # Object-key order is irrelevant; array order, JSON types and every value
    # are retained. NaN/non-JSON values fail closed.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def diff_paths(left, right, path='', out=None):
    out = [] if out is None else out
    if len(out) >= 40 or signature(left) == signature(right):
        return out
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            pointer = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
            if key not in left or key not in right:
                out.append(pointer)
            else:
                diff_paths(left[key], right[key], pointer, out)
            if len(out) >= 40:
                break
    elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        for index, (a, b) in enumerate(zip(left, right)):
            diff_paths(a, b, path + '/' + str(index), out)
            if len(out) >= 40:
                break
    else:
        out.append(path or '/')
    return out


def compare_excluded_day(before, proposed):
    result = {'passed': False, 'stale_unverified_on_both': False,
              'derived_reason_normalized': False, 'original_diff_paths': [], 'remaining_diff_paths': [],
              'boundary': 'Only PLANNING_INPUT_CHANGED versus SCHEDULE_DEPENDENCIES_CHANGED may differ; both records must remain explicitly stale/unverified. Every other day field is compared exactly.'}
    try:
        result['original_diff_paths'] = diff_paths(before, proposed)
        if not isinstance(before, dict) or not isinstance(proposed, dict):
            return result
        left, right = before.get('feasibility'), proposed.get('feasibility')
        if not isinstance(left, dict) or not isinstance(right, dict):
            return result
        result['stale_unverified_on_both'] = (before.get('schedule_stale') is True
            and proposed.get('schedule_stale') is True
            and left.get('status') == right.get('status') == 'unverified')
        codes = (left.get('solver_status'), right.get('solver_status'))
        result['derived_reasons'] = list(codes)
        if not result['stale_unverified_on_both'] or any(code not in ALLOWED_REASONS for code in codes):
            return result
        if signature(before) == signature(proposed):
            result['passed'] = True
            return result
        if set(codes) != ALLOWED_REASONS:
            result['remaining_diff_paths'] = result['original_diff_paths']
            return result
        a, b = copy.deepcopy(before), copy.deepcopy(proposed)
        a['feasibility']['solver_status'] = b['feasibility']['solver_status'] = 'REVIEWED_DERIVED_STALE_REASON'
        result['derived_reason_normalized'] = True
        result['remaining_diff_paths'] = diff_paths(a, b)
        result['passed'] = signature(a) == signature(b)
        return result
    except (TypeError, ValueError):
        result['error'] = 'Non-JSON comparison input refused'
        return result
