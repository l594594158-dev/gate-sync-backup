#!/usr/bin/env python3
"""
gatedaidan 五策略健康检查 v1.3
每5分钟独立检查 BTC + HYPE + ZEC + NEAR + XLM 五个策略进程
异常时自动重启，日志独立
"""

import os
import sys
import subprocess
import time
from datetime import datetime

BASE_DIR = '/root/liucangyang'
LOG_FILE = f'{BASE_DIR}/logs/health_check.log'
BTC_SCRIPT = f'{BASE_DIR}/auto_trade.py'
HYPE_SCRIPT = f'{BASE_DIR}/auto_trade_hype.py'
ZEC_SCRIPT = f'{BASE_DIR}/auto_trade_zec.py'
NEAR_SCRIPT = f'{BASE_DIR}/auto_trade_near.py'
XLM_SCRIPT = f'{BASE_DIR}/auto_trade_xlm.py'

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def check_process(keyword):
    """检查指定脚本是否在运行，返回 (alive, pids)"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', keyword],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            return True, pids
        return False, []
    except:
        return False, []

def start_script(script_path, log_path):
    """启动策略脚本，返回 PID"""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a') as log_f:
            proc = subprocess.Popen(
                ['python3', '-B', '-u', script_path],
                stdout=log_f, stderr=subprocess.STDOUT,
                cwd=BASE_DIR
            )
        return proc.pid
    except Exception as e:
        log(f"启动 {script_path} 失败: {e}")
        return None

def clean_orphan_orders():
    """清理孤儿条件单: size正(平空)需SHORT仓, size负(平多)需LONG仓"""
    try:
        import ccxt
        g = ccxt.gate({
            'apiKey': 'a261449031d180a2bd5537390261a461',
            'secret': 'b981402bbeb1f6d7f9ea878bd1972cd0cd7a900de437e5a27646c2c780837d8b',
            'options': {'defaultType': 'swap'}
        })
        g.load_markets()

        contracts = [
            ('BTC_USDT', 'BTC/USDT:USDT', 'BTC'),
            ('HYPE_USDT', 'HYPE/USDT:USDT', 'HYPE'),
            ('ZEC_USDT', 'ZEC/USDT:USDT', 'ZEC'),
            ('NEAR_USDT', 'NEAR/USDT:USDT', 'NEAR'),
            ('XLM_USDT', 'XLM/USDT:USDT', 'XLM'),
        ]
        total_cleaned = 0
        for contract, symbol, name in contracts:
            # 查持仓方向
            has_long = has_short = False
            try:
                for p in g.fetch_positions(symbols=[symbol]):
                    if float(p.get('contracts', 0)) > 0:
                        if p.get('side') == 'long': has_long = True
                        else: has_short = True
            except:
                continue

            # 查条件单
            try:
                cond = g.private_futures_get_settle_price_orders({
                    'settle': 'usdt', 'status': 'open', 'contract': contract
                })
            except:
                continue

            cleaned = 0
            for o in cond:
                size_val = int(o.get('initial', {}).get('size', 0))
                # size负=平多→需LONG仓; size正=平空→需SHORT仓
                if (size_val < 0 and not has_long) or (size_val > 0 and not has_short):
                    try:
                        g.private_futures_delete_settle_price_orders_order_id(
                            {'settle': 'usdt', 'order_id': o['id']})
                        cleaned += 1
                    except:
                        pass
            if cleaned > 0:
                log(f"🧹 {name}: 清理{cleaned}条孤儿条件单")
                total_cleaned += cleaned

        return total_cleaned
    except Exception as e:
        log(f"⚠️ 孤儿单检查失败: {e}")
        return 0

# ========== 状态文件同步 ==========
def sync_state_files():
    """检查交易所持仓与本地状态文件一致性，不一致时修正"""
    try:
        import json
        from api_config import TRADE_API_KEY, TRADE_SECRET
        import ccxt
        
        g = ccxt.gate({
            'apiKey': TRADE_API_KEY,
            'secret': TRADE_SECRET,
            'options': {'defaultType': 'swap', 'settle': 'usdt'}
        })
        
        state_map = {
            'ZEC/USDT:USDT': f'{BASE_DIR}/databases/state_zec.json',
            'NEAR/USDT:USDT': f'{BASE_DIR}/databases/state_near.json',
            'HYPE/USDT:USDT': f'{BASE_DIR}/databases/state_hype.json',
            'BTC/USDT:USDT': f'{BASE_DIR}/databases/state.json',
            'XLM/USDT:USDT': f'{BASE_DIR}/databases/state_xlm.json',
        }
        
        positions = g.fetch_positions()
        
        for symbol, state_file in state_map.items():
            if not os.path.exists(state_file):
                continue
            
            with open(state_file) as f:
                state = json.load(f)
            
            exch_long = None
            exch_short = None
            for p in positions:
                if p.get('symbol') == symbol:
                    qty = float(p.get('contracts', 0))
                    if qty > 0:
                        entry = float(p.get('entryPrice', 0))
                        if p.get('side') == 'long':
                            exch_long = {'entry': entry, 'qty': qty}
                        else:
                            exch_short = {'entry': entry, 'qty': qty}
            
            changed = False
            
            if state.get('long_pos') and not exch_long:
                log(f"🔄 {symbol}: LONG已平仓，清除状态")
                state['long_pos'] = None
                changed = True
            elif exch_long and not state.get('long_pos'):
                log(f"🔄 {symbol}: 恢复LONG @ {exch_long['entry']}")
                state['long_pos'] = {'entry': exch_long['entry'], 'signal': '健康检查恢复', 'open_time': '', 'sl_tp_placed': False, 'sl_tp_entry': exch_long['entry']}
                changed = True
            
            if state.get('short_pos') and not exch_short:
                log(f"🔄 {symbol}: SHORT已平仓，清除状态")
                state['short_pos'] = None
                changed = True
            elif exch_short and not state.get('short_pos'):
                log(f"🔄 {symbol}: 恢复SHORT @ {exch_short['entry']}")
                state['short_pos'] = {'entry': exch_short['entry'], 'signal': '健康检查恢复', 'open_time': '', 'sl_tp_placed': False, 'sl_tp_entry': exch_short['entry']}
                changed = True
            
            if changed:
                with open(state_file, 'w') as f:
                    json.dump(state, f)
    except Exception as e:
        log(f"⚠️ 状态同步失败: {e}")

def main():
    log("══════ gatedaidan 健康检查 ══════")

    # 1. BTC 策略
    alive, pids = check_process('auto_trade.py')
    if alive:
        log(f"✅ BTC 策略正常 | PID: {','.join(pids)}")
    else:
        log("❌ BTC 策略未运行，自动重启...")
        pid = start_script(BTC_SCRIPT, f'{BASE_DIR}/logs/auto_trade.log')
        if pid:
            log(f"✅ BTC 策略已重启 | PID: {pid}")
        else:
            log("💀 BTC 策略重启失败")

    # 2. HYPE 策略
    alive, pids = check_process('auto_trade_hype.py')
    if alive:
        log(f"✅ HYPE 策略正常 | PID: {','.join(pids)}")
    else:
        log("❌ HYPE 策略未运行，自动重启...")
        pid = start_script(HYPE_SCRIPT, f'{BASE_DIR}/logs/auto_trade_hype.log')
        if pid:
            log(f"✅ HYPE 策略已重启 | PID: {pid}")
        else:
            log("💀 HYPE 策略重启失败")

    # 3. ZEC 策略
    alive, pids = check_process('auto_trade_zec.py')
    if alive:
        log(f"✅ ZEC 策略正常 | PID: {','.join(pids)}")
    else:
        log("❌ ZEC 策略未运行，自动重启...")
        pid = start_script(ZEC_SCRIPT, f'{BASE_DIR}/logs/auto_trade_zec.log')
        if pid:
            log(f"✅ ZEC 策略已重启 | PID: {pid}")
        else:
            log("💀 ZEC 策略重启失败")

    # 4. NEAR 策略
    alive, pids = check_process('auto_trade_near.py')
    if alive:
        log(f"✅ NEAR 策略正常 | PID: {','.join(pids)}")
    else:
        log("❌ NEAR 策略未运行，自动重启...")
        pid = start_script(NEAR_SCRIPT, f'{BASE_DIR}/logs/auto_trade_near.log')
        if pid:
            log(f"✅ NEAR 策略已重启 | PID: {pid}")
        else:
            log("💀 NEAR 策略重启失败")

    # 5. XLM 策略
    alive, pids = check_process('auto_trade_xlm.py')
    if alive:
        log(f"✅ XLM 策略正常 | PID: {','.join(pids)}")
    else:
        log("❌ XLM 策略未运行，自动重启...")
        pid = start_script(XLM_SCRIPT, f'{BASE_DIR}/logs/auto_trade_xlm.log')
        if pid:
            log(f"✅ XLM 策略已重启 | PID: {pid}")
        else:
            log("💀 XLM 策略重启失败")

    # 5. 孤儿条件单清理
    clean_orphan_orders()

    # 6. 状态文件与交易所持仓一致性同步
    sync_state_files()

    log("══════ 检查完成 ══════")

if __name__ == '__main__':
    main()
