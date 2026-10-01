"""Local contract stub. Never imported by deployed application code."""
import copy

from test_swiggy_live import FakeLive


class FakeBasket(FakeLive):
    def __init__(self):
        super().__init__()
        self.dishes = {'Mini Tiffin': 12500, 'Veg Meals': 18000, 'Modern Meal': 15000}
        self.modern = False
        self.required_addons = 0
        self.addon_stock = True

    def tool(self, name, args):
        if name == 'search_menu':
            result = super().tool(name, args)
            for item in result['structuredContent']['data']['items']:
                if item['menu_item_id'] in ('m1', 'm2'):
                    item['hasVariants'] = item['hasAddons'] = True
                    choices = [{'id': 'small', 'groupId': 'size', 'name': 'Small', 'price': 0, 'inStock': True, 'isVeg': True},
                               {'id': 'large', 'groupId': 'size', 'name': 'Large', 'price': 20, 'inStock': True, 'isVeg': True},
                               {'id': 'sold-out', 'groupId': 'size', 'name': 'Unavailable', 'inStock': False}]
                    if item['menu_item_id'] == 'm2' or self.modern:
                        item['variantsV2'] = [{'groupId': 'size', 'name': 'Size', 'variations': choices}]
                    else:
                        item['variations'] = choices
            return result
        if name == 'update_food_cart':
            assert args['restaurantId'] == 'r-1'
            self.cart = copy.deepcopy(args['cartItems'])
            return {'structuredContent': {'success': True}}
        if name == 'get_food_cart' and self.cart:
            items, total = [], 0
            for line in self.cart:
                index = int(line['menu_item_id'][1:])
                dish, price = list(self.dishes.items())[index]
                extra = 20 if any(v.get('variation_id', v.get('variationId')) == 'large' for v in line.get('variants', line.get('variantsV2', []))) else 0
                value = line['quantity'] * (price / 100 + extra + 10 * len(line.get('addons', [])))
                total += value
                items.append({**copy.deepcopy(line), 'name': dish, 'is_veg': True, 'in_stock': True, 'total': value,
                    'valid_addons': [{'group_id': 'extras', 'name': 'Extras', 'minAddons': self.required_addons,
                                     'maxAddons': 1, 'choices': [{'id': 'curd', 'name': 'Curd', 'inStock': self.addon_stock, 'isVeg': True}]}]
                    if index else []})
            return {'structuredContent': {'success': True, 'data': {'addressId': args['addressId'], 'data': {
                'restaurant': {'id': 'r-1', 'name': 'Hotel Saravana Bhavan (Adyar)'}, 'items': items,
                'pricing': {'item_total': total, 'delivery_charge': 35, 'to_pay': total + 35}}}}}
        return super().tool(name, args)
