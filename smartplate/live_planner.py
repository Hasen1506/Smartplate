"""Bounded live-menu MILP. Integer paise; coverage always precedes preferences.

No generated menus, nutritional guesses or purchasing side effects. CBC's output
is checked before it can become a plan, including on timeout or solver failure.
"""
from decimal import Decimal, ROUND_HALF_UP
import time
import math

import pulp


def paise(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def valid_incumbent(problem):
    for variable in problem.variables():
        if variable.name == "__dummy":
            continue
        value = variable.varValue
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        if variable.cat != pulp.LpContinuous and abs(value - round(value)) > 1e-5:
            return False
        if variable.lowBound is not None and value < variable.lowBound - 1e-5:
            return False
        if variable.upBound is not None and value > variable.upBound + 1e-5:
            return False
    return all(constraint.valid(1e-5) for constraint in problem.constraints.values())


def solve(slots, candidates, budget, reserve, *, preferences=None, previous=None, seconds=6):
    """Maximise covered slots, then useful variety/preferences/stability, then cost.

    Soft variety has no repeat cap: a tight budget can repeat an affordable meal.
    Hard avoid preferences and user-confirmed meal suitability apply first.
    The fallback is feasible, deterministic, and explicitly not certified optimal.
    """
    preferences, previous = preferences or {}, previous or {}
    candidates = sorted(candidates, key=lambda c: (paise(c['price']), c['restaurant_id'], c['id']))
    def key(c):
        return c['restaurant_id'] + ':' + c['id']
    def eligible(c, slot):
        pref = preferences.get(key(c), {})
        return pref.get('preference') != 'avoid' and (not pref.get('suitable_meals') or slot['meal'] in pref['suitable_meals'])
    cap, fee = max(0, paise(budget)), paise(reserve)
    costs = [paise(c['price']) + fee for c in candidates]
    allowed = [[j for j, c in enumerate(candidates) if eligible(c, s)] for s in slots]
    minimum = sum(min((costs[j] for j in js), default=0) for js in allowed) if all(allowed) else None
    picks, spent = [], 0
    # Cheapest-first baseline covers as many slots as possible for uniform costs;
    # for heterogeneous eligibility it is only a safe fallback, never labelled optimal.
    for js in allowed:
        chosen = next((j for j in js if spent + costs[j] <= cap), None)
        picks.append(chosen)
        if chosen is not None:
            spent += costs[chosen]
    best = picks[:]
    problem = pulp.LpProblem('live_menu_week', pulp.LpMaximize)
    x = {(i, j): pulp.LpVariable(f'm_{i}_{j}', cat='Binary') for i, js in enumerate(allowed) for j in js}
    for i in range(len(slots)):
        problem += pulp.lpSum(x[i, j] for j in allowed[i]) <= 1
    cost = pulp.lpSum(costs[j] * v for (i, j), v in x.items())
    coverage = pulp.lpSum(x.values())
    problem += cost <= cap
    used = {j: pulp.LpVariable(f'dish_{j}', cat='Binary') for j in range(len(candidates)) if any(j in js for js in allowed)}
    for j, v in used.items():
        variables = [x[i, j] for i in range(len(slots)) if (i, j) in x]
        problem += pulp.lpSum(variables) >= v
        problem += pulp.lpSum(variables) <= len(slots) * v
    days = sorted({s['date'] for s in slots})
    day_vars = [pulp.LpVariable(f'day_{n}', cat='Binary') for n in range(len(days))]
    for day, variable in zip(days, day_vars):
        variables = [v for (i, j), v in x.items() if slots[i]['date'] == day]
        problem += pulp.lpSum(variables) >= variable
        problem += pulp.lpSum(variables) <= len(slots) * variable
    day_coverage = pulp.lpSum(day_vars)
    quality = pulp.lpSum(used.values()) * 2 + pulp.lpSum(
        v * (3 * (preferences.get(key(candidates[j]), {}).get('preference') == 'like')
             + (previous.get(slots[i]['date'] + ':' + slots[i]['meal']) == key(candidates[j])))
        for (i, j), v in x.items())
    deadline, stages, certified = time.monotonic() + seconds, [], False
    if x:
        for name, objective in [('coverage', coverage), ('day_coverage', day_coverage), ('preferences', quality), ('cost', -cost)]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            problem.setObjective(objective)
            try:
                problem.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=remaining, threads=1, gapRel=0))
            except (pulp.PulpSolverError, OSError):
                break
            if not valid_incumbent(problem):
                break
            chosen = [next((j for j in js if x[i, j].value() > .5), None) for i, js in enumerate(allowed)]
            if sum(j is not None for j in chosen) >= sum(j is not None for j in best):
                best = chosen
            optimal = problem.status == pulp.LpStatusOptimal and problem.sol_status == pulp.LpSolutionOptimal
            stages.append({'stage': name, 'optimal': optimal})
            if name == 'coverage':
                certified = optimal
            if not optimal:
                break
            problem += objective == round(pulp.value(objective) or 0)
    total = sum(costs[j] for j in best if j is not None)
    if total > cap or any(j is not None and j not in allowed[i] for i, j in enumerate(best)):
        raise RuntimeError('Planner produced an invalid budget or meal selection')
    return {'items': [candidates[j] if j is not None else None for j in best],
            'estimated_total': total / 100, 'covered': sum(j is not None for j in best),
            'required': len(slots), 'minimum_full_coverage': minimum / 100 if minimum is not None else None,
            'shortfall': max(0, minimum - cap) / 100 if minimum is not None else None,
            'solver': {'engine': 'CBC MILP', 'coverage_optimal': certified, 'stages': stages,
                       'fallback': not stages, 'time_limit_seconds': seconds}}
