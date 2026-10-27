"""Resolve SARIF 2.1.0 CodeQL driver/extension references without guessing.

OASIS SARIF 2.1.0 sections 3.52 and 3.54 define component-local descriptor
indexes, optional GUID identity, and consistency of redundant identifiers.
https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html
"""
import math


def _index(value, sequence, description):
    if type(value) is not int or not 0 <= value < len(sequence):
        raise RuntimeError('Invalid/unavailable SARIF ' + description + ' index')
    return sequence[value]


def _guid(value, candidate):
    return isinstance(value, str) and isinstance(candidate, str) and value.casefold() == candidate.casefold()


def resolve_rule(tool, result):
    if not isinstance(result, dict):
        raise RuntimeError('Invalid SARIF result')
    driver, extensions = tool.get('driver'), tool.get('extensions', [])
    if not isinstance(driver, dict) or not isinstance(extensions, list) or any(not isinstance(c, dict) for c in extensions):
        raise RuntimeError('Invalid SARIF tool components')
    reference = result.get('rule', {})
    if not isinstance(reference, dict):
        raise RuntimeError('Invalid SARIF reportingDescriptor reference')
    component_ref = reference.get('toolComponent', {})
    if not isinstance(component_ref, dict):
        raise RuntimeError('Invalid SARIF toolComponent reference')
    if 'index' in component_ref:
        component = _index(component_ref['index'], extensions, 'toolComponent')
    elif 'guid' in component_ref:
        candidates = [c for c in [driver, *extensions] if _guid(component_ref['guid'], c.get('guid'))]
        if len(candidates) != 1:
            raise RuntimeError('SARIF toolComponent GUID missing or ambiguous')
        component = candidates[0]
    else:
        component = driver
    if ('name' in component_ref and component_ref['name'] != component.get('name')
            or 'guid' in component_ref and not _guid(component_ref['guid'], component.get('guid'))):
        raise RuntimeError('SARIF toolComponent identifiers disagree')
    rules = component.get('rules', [])
    if not isinstance(rules, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id'] for r in rules):
        raise RuntimeError('Invalid SARIF rule descriptors')
    for outer, inner in [('ruleId', 'id'), ('ruleIndex', 'index')]:
        if outer in result and inner in reference and result[outer] != reference[inner]:
            raise RuntimeError('SARIF result and rule reference identifiers disagree')
    for value, key in [(result, 'ruleIndex'), (reference, 'index')]:
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise RuntimeError('Invalid SARIF rule index type/range')
    identifier = reference.get('id', result.get('ruleId'))
    if identifier is not None and (not isinstance(identifier, str) or not identifier):
        raise RuntimeError('Invalid SARIF rule identifier')
    index = reference.get('index', result.get('ruleIndex'))
    if index is not None:
        rule = _index(index, rules, 'rule')
    else:
        candidates = rules
        if 'guid' in reference:
            candidates = [r for r in candidates if _guid(reference['guid'], r.get('guid'))]
        elif identifier is not None:
            candidates = [r for r in candidates if r['id'] == identifier]
        else:
            raise RuntimeError('SARIF result has no resolvable rule reference')
        if len(candidates) != 1:
            raise RuntimeError('SARIF rule missing or ambiguous in the selected component')
        rule = candidates[0]
    if 'guid' in reference and not _guid(reference['guid'], rule.get('guid')):
        raise RuntimeError('SARIF rule GUID and index disagree')
    # A result may name one additional hierarchical sub-rule component (§3.52.4).
    if identifier is not None and identifier != rule['id']:
        prefix = rule['id'] + '/'
        if not identifier.startswith(prefix) or not identifier[len(prefix):] or '/' in identifier[len(prefix):]:
            raise RuntimeError('SARIF rule ID and descriptor index disagree')
    return rule


def is_blocking(rule, result):
    properties = rule.get('properties', {})
    defaults = rule.get('defaultConfiguration', {})
    if not isinstance(properties, dict) or not isinstance(defaults, dict):
        raise RuntimeError('Invalid CodeQL rule severity metadata')
    level = result.get('level', defaults.get('level', 'warning'))
    if level not in ('none', 'note', 'warning', 'error'):
        raise RuntimeError('Invalid CodeQL result severity level')
    if 'security-severity' not in properties:
        if 'security' in properties.get('tags', []):
            raise RuntimeError('CodeQL security rule lacks numeric security severity')
        # Preserve non-security diagnostic behavior; an error remains blocking.
        return level == 'error'
    raw = properties['security-severity']
    try:
        if isinstance(raw, bool):
            raise ValueError('boolean severity')
        severity = float(raw)
    except (ValueError, TypeError) as error:
        raise RuntimeError('Invalid CodeQL security severity') from error
    if not math.isfinite(severity) or not 0 <= severity <= 10:
        raise RuntimeError('Invalid CodeQL security severity range')
    return severity >= 7 or (severity == 0 and level == 'error')
