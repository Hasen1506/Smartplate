"""Exact multi-item review and explicit two-stage variant/add-on selection."""
import hashlib
import json

from . import db
from .integrations import swiggy_live as live


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def groups(options):
    legacy, modern = options.get('variations') or [], options.get('variantsV2') or []
    if not isinstance(legacy, list) or not isinstance(modern, list) or any(not isinstance(g, dict) for g in legacy + modern):
        raise live.SwiggyError('Swiggy returned malformed variant details')
    if legacy and modern:
        raise live.SwiggyError('Swiggy returned conflicting customization formats')
    result = []
    if legacy:
        grouped = {}
        for choice in legacy:
            gid = str(choice.get('groupId') or '')
            if not gid or not choice.get('id'):
                raise live.SwiggyError('Swiggy did not identify this variant option')
            grouped.setdefault(gid, []).append(choice)
        result = [{'id': gid, 'name': 'Variant', 'choices': choices} for gid, choices in grouped.items()]
    elif modern:
        result = [{'id': str(g.get('groupId') or ''), 'name': g.get('name') or 'Variant',
                   'choices': g.get('variations') or []} for g in modern]
    if live._flag(options.get('hasVariants')) is not False and not result:
        raise live.SwiggyError('Swiggy did not return enough variant details to select this item')
    if len({g['id'] for g in result}) != len(result):
        raise live.SwiggyError('Swiggy returned duplicate variant groups')
    for group in result:
        if not isinstance(group['choices'], list) or any(not isinstance(c, dict) for c in group['choices']):
            raise live.SwiggyError('Swiggy returned malformed variant choices')
        group['choices'] = [c for c in group['choices'] if live._flag(c.get('inStock')) is not False]
        if not group['id'] or not group['choices'] or any(not c.get('id') for c in group['choices']):
            raise live.SwiggyError('A required variant is unavailable or unidentified')
        if len({str(c['id']) for c in group['choices']}) != len(group['choices']):
            raise live.SwiggyError('Swiggy returned duplicate variant choices')
    return result, 'variantsV2' if modern else 'variants'


def details(user_id, body):
    p = live.live_item_details(user_id, body.get('restaurant_id', ''), body.get('restaurant_name', ''),
                               body.get('item_id', ''), body.get('item_name', ''))
    p['groups'], p['variant_format'] = groups(p['customizations'])
    from .domain import models
    if models.get_user(user_id)['diet'] == 'veg':
        for group in p['groups']:
            group['choices'] = [c for c in group['choices'] if live._flag(c.get('isVeg')) is not False]
            if not group['choices']:
                raise live.SwiggyError('No available vegetarian variant was returned')
    return p


def preview(user_id, body):
    items = body.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        raise ValueError('Review between one and ten menu items from one restaurant')
    lines, checks, seen = [], [], set()
    for selected in items:
        if not isinstance(selected, dict):
            raise ValueError('Choose valid menu items')
        p = details(user_id, {**selected, 'restaurant_id': body.get('restaurant_id'),
                             'restaurant_name': body.get('restaurant_name')})
        qty = selected.get('quantity', 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 10:
            raise ValueError('Quantity must be between one and ten')
        if p['item_id'] in seen:
            raise ValueError('Combine quantities of the same dish into one reviewed line')
        seen.add(p['item_id'])
        chosen = selected.get('variants', {})
        if not isinstance(chosen, dict) or set(chosen) != {g['id'] for g in p['groups']}:
            raise ValueError('Choose one option from each required variant group')
        variants = []
        for group in p['groups']:
            choice = next((c for c in group['choices'] if str(c['id']) == chosen[group['id']]), None)
            if not choice:
                raise ValueError('Choose a currently available variant')
            variants.append({'group_id': group['id'], 'choice_id': str(choice['id']), 'name': choice.get('name') or 'Variant'})
        if qty > 1 and (variants or p['customizations'].get('hasAddons')) and selected.get('same_options') is not True:
            raise ValueError('Confirm the same options for every portion, or use separate orders')
        lines.append({'id': p['item_id'], 'name': p['item'], 'quantity': qty,
                      'variants': variants, 'variant_format': p['variant_format'], 'addons': []})
        checks.append({'item': p['item_id'], 'price': p['menu_price'], 'options': p['customizations']})
    if sum(l['quantity'] for l in lines) > 20:
        raise ValueError('Review at most twenty portions')
    out = {'restaurant_id': p['restaurant_id'], 'restaurant_name': p['restaurant'],
           'address_id': p['address_id'], 'address': p['address'], 'lines': lines}
    return {**out, 'fingerprint': fingerprint({'review': out, 'checks': checks}),
            'note': 'Add-ons are offered only after Swiggy confirms which are valid for these variants.'}


def saved(user_id):
    with db.cursor() as cur:
        row = cur.execute('SELECT payload FROM swiggy_cart_lines WHERE user_id=?', (user_id,)).fetchone()
    return db.jl(row['payload'], {}) if row else None


def selections(values, addons=False):
    out = []
    for v in values or []:
        if not isinstance(v, dict):
            return None
        group = v.get('group_id') or v.get('groupId')
        choice = v.get('variation_id') or v.get('variationId') or v.get('choice_id') or v.get('id')
        if not group or not choice or (addons and v.get('quantity', 1) != 1):
            return None
        out.append((str(group), str(choice)))
    return sorted(out)


def matches(user_id, cart, address_id):
    intent = saved(user_id)
    if not isinstance(cart.get('restaurant'), dict):
        return False
    if not intent or intent['address_id'] != address_id or str((cart.get('restaurant') or {}).get('id')) != intent['restaurant_id']:
        return False
    items = cart['items']
    if len(items) != len(intent['lines']):
        return False
    for line in intent['lines']:
        rows = [i for i in items if str(live._get(i, 'menu_item_id')) == line['id']]
        if len(rows) != 1 or rows[0].get('quantity') != line['quantity']:
            return False
        item = rows[0]
        if (selections(item.get('variants') or item.get('variantsV2')) != selections(line['variants'])
                or selections(item.get('addons'), True) != selections(line['addons'], True)):
            return False
    return True


def _write(user_id, intent):
    with db.cursor() as cur:
        cur.execute('INSERT OR REPLACE INTO swiggy_cart_lines VALUES (?,?)', (user_id, db.jd(intent)))
        cur.execute('INSERT OR REPLACE INTO swiggy_cart_intents VALUES (?,?,?,?,?)',
                    (user_id, intent['address_id'], intent['restaurant_id'], intent['restaurant_name'], intent['lines'][0]['id']))
        cur.execute('DELETE FROM swiggy_checkout_quotes WHERE user_id=?', (user_id,))


def wire_items(tool, lines):
    result = []
    for line in lines:
        item = live._cart_item(tool, line['id'])
        quantity_key = next(k for k in item if k in ('quantity', 'qty', 'count'))
        item[quantity_key] = line['quantity']
        if line['variants']:
            group_key, choice_key = ('groupId', 'variationId') if line['variant_format'] == 'variantsV2' else ('group_id', 'variation_id')
            item[line['variant_format']] = [{group_key: v['group_id'], choice_key: v['choice_id']} for v in line['variants']]
        if line['addons']:
            item['addons'] = [{'group_id': a['group_id'], 'id': a['choice_id'], 'quantity': 1} for a in line['addons']]
        result.append(item)
    return result


def update(user_id, intent):
    conn = live._conn(user_id)
    tool = live._tool(conn, 'update_food_cart')
    live.call(user_id, 'update_food_cart', live.build_args(tool, {'cart_items': wire_items(tool, intent['lines']),
        'restaurant': intent['restaurant_id'], 'restaurant_name': intent['restaurant_name'], 'address': intent['address_id']}))
    data = live.call(user_id, 'get_food_cart', live.build_args(live._tool(conn, 'get_food_cart'),
                       {'address': intent['address_id'], 'restaurant_name': intent['restaurant_name']}))
    cart = live._cart_view(data, intent['address_id'])
    _write(user_id, intent)
    if not matches(user_id, cart, intent['address_id']):
        with db.cursor() as cur:
            cur.execute('DELETE FROM swiggy_cart_lines WHERE user_id=?', (user_id,))
            cur.execute('DELETE FROM swiggy_cart_intents WHERE user_id=?', (user_id,))
        raise live.SwiggyError('The updated cart did not match your review. Check it in Swiggy before continuing.')
    return live.current_live_cart(user_id)['cart']


def prepare(user_id, body):
    p = preview(user_id, body)
    if body.get('expected_fingerprint') != p['fingerprint']:
        raise live.CartChanged('Menu, options, quantity or address changed. Review the whole basket again.')
    conn = live._conn(user_id)
    data = live.call(user_id, 'get_food_cart', live.build_args(live._tool(conn, 'get_food_cart'), {'address': p['address_id']}))
    if live._cart_view(data, p['address_id'])['items']:
        raise live.SwiggyError('Your Swiggy cart already has items. Review or clear it in Swiggy first.')
    return update(user_id, p)


def addon_review(user_id):
    conn, _, cart = live._prepared_cart(user_id)
    intent = saved(user_id)
    if not intent:
        raise ValueError('Prepare a reviewed basket before adding options')
    groups_by_item = {}
    for item in cart['items']:
        groups_by_item[str(live._get(item, 'menu_item_id'))] = valid_addon_groups(item)
    return {'groups': groups_by_item, 'fingerprint': fingerprint(cart), 'lines': intent['lines']}


def valid_addon_groups(item):
    groups = item.get('valid_addons') or []
    if not isinstance(groups, list):
        raise live.SwiggyError('Swiggy returned malformed add-on groups')
    seen = set()
    for group in groups:
        if not isinstance(group, dict):
            raise live.SwiggyError('Swiggy returned malformed add-on groups')
        gid = str(group.get('group_id') or group.get('groupId') or '')
        choices = group.get('choices') or group.get('addons') or []
        if (not gid or gid in seen or not isinstance(choices, list)
                or any(not isinstance(c, dict) or not c.get('id') for c in choices)):
            raise live.SwiggyError('Swiggy did not identify the allowed add-ons')
        seen.add(gid)
        lo, hi = group.get('minAddons', 0), group.get('maxAddons', len(choices))
        if (isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, int)
                or not isinstance(hi, int) or not 0 <= lo <= hi <= len(choices)):
            raise live.SwiggyError('Swiggy returned invalid add-on limits')
    return groups


def add_addons(user_id, body):
    review = addon_review(user_id)
    if body.get('expected_fingerprint') != review['fingerprint']:
        raise live.CartChanged('The cart or allowed add-ons changed. Review them again.')
    intent = saved(user_id)
    selected = body.get('selections')
    if not isinstance(selected, dict) or set(selected) != set(review['groups']):
        raise ValueError('Review add-ons for every basket item')
    for line in intent['lines']:
        chosen = selected[line['id']]
        if not isinstance(chosen, dict):
            raise ValueError('Choose valid add-on groups')
        line['addons'] = []
        valid = review['groups'][line['id']]
        if set(chosen) - {str(g.get('group_id') or g.get('groupId')) for g in valid}:
            raise ValueError('An add-on group is not valid for these variants')
        for group in valid:
            gid = str(group.get('group_id') or group.get('groupId') or '')
            ids = chosen.get(gid, [])
            choices = group.get('choices') or group.get('addons') or []
            if not gid or not isinstance(ids, list) or len(ids) != len(set(ids)):
                raise ValueError('Choose identified add-ons without duplicates')
            if not group.get('minAddons', 0) <= len(ids) <= group.get('maxAddons', len(choices)):
                raise ValueError('Respect the minimum and maximum add-ons for each group')
            for cid in ids:
                choice = next((c for c in choices if str(c.get('id')) == cid), None)
                if not choice:
                    raise ValueError('That add-on is unavailable for this variant')
                from .domain import models
                if live._flag(choice.get('inStock')) is False or (models.get_user(user_id)['diet'] == 'veg' and live._flag(choice.get('isVeg')) is False):
                    raise ValueError('That add-on does not match stock or vegetarian rules')
                line['addons'].append({'group_id': gid, 'choice_id': cid, 'name': choice.get('name') or 'Add-on'})
        if line['quantity'] > 1 and line['addons'] and body.get('same_options') is not True:
            raise ValueError('Confirm the same add-ons for all portions')
    return update(user_id, intent)
