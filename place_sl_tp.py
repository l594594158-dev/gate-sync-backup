import ccxt
g = ccxt.gate({
    'apiKey': 'a261449031d180a2bd5537390261a461',
    'secret': 'b981402bbeb1f6d7f9ea878bd1972cd0cd7a900de437e5a27646c2c780837d8b',
    'options': {'defaultType': 'swap'}
})
g.load_markets()

# 查持仓
for p in g.fetch_positions(symbols=['BTC/USDT:USDT']):
    if float(p.get('contracts', 0)) > 0:
        entry = float(p['entryPrice'])
        qty = int(p['contracts'])
        print(f'持仓: {p["side"]} {qty}张 @ ${entry:.1f}')

# 全撤
for o in g.fetch_open_orders('BTC/USDT:USDT'):
    g.cancel_order(o['id'], 'BTC/USDT:USDT')
for o in g.private_futures_get_settle_price_orders({'settle': 'usdt', 'status': 'open', 'contract': 'BTC_USDT'}):
    g.private_futures_delete_settle_price_orders_order_id({'settle': 'usdt', 'order_id': o['id']})
print('已撤全部旧单')

# 挂单
sl = round(entry * 1.015, 1)
tp = round(entry * 0.975, 1)

# SL: 条件单 direction=short
g.private_futures_post_settle_price_orders({
    'settle': 'usdt',
    'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(sl), 'rule': 1, 'expiration': 0},
    'initial': {'contract': 'BTC_USDT', 'size': qty, 'price': '0', 'tif': 'ioc', 'direction': 'short'}
})
print(f'SL: ${sl}')

# TP: 限价 reduceOnly
g.create_order('BTC/USDT:USDT', 'limit', 'buy', qty, tp, params={'reduceOnly': True})
print(f'TP: ${tp}')

print(f"SL确认: {len(g.private_futures_get_settle_price_orders({'settle': 'usdt', 'status': 'open', 'contract': 'BTC_USDT'}))}")
print(f"TP确认: {len(g.fetch_open_orders('BTC/USDT:USDT'))}")
