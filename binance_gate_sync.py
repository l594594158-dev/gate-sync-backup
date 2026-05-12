#!/usr/bin/env python3
"""
币安 -> Gate.io 仓位同步监控
同步净方向（多空相抵后的方向）
逐仓模式
"""

import time
import logging
import os
from binance.client import Client as BinanceClient
from gate_api.configuration import Configuration
from gate_api.api_client import ApiClient
from gate_api.api.futures_api import FuturesApi
from gate_api.models import FuturesOrder

# 配置
BINANCE_API_KEY = "yoIUcS0f687fbduzdLGRHgECeexVmnvVjhegkPeVYrACvRVYuFJWSJDjfhgBE8ww"
BINANCE_SECRET_KEY = "BpsTBrNNQ6T4r35GNKhA6uWRg4asAu1cRuGOzF7jYM5GRR32kUPHluiis7XXyZ8t"
GATE_API_KEY = "6798f83ac4f0952585c8fbc28f649320"
GATE_SECRET_KEY = "b9ec180b4290b0157288604afd66046f51b011a711bf8a24b599055e91f2f393"

SYMBOL = "BTCUSDT"
GATE_CONTRACT = "BTC_USDT"
LEVERAGE = 20
CONTRACT_RATIO = 10000  # 币安BTC / Gate 0.0001BTC

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s',
                    handlers=[
                        logging.FileHandler('/tmp/sync.log'),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger(__name__)

# 币安仓位日志
binance_log = logging.getLogger('binance')
binance_log.setLevel(logging.INFO)
log_handler = logging.FileHandler('/tmp/binance_positions.log')
log_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
binance_log.addHandler(log_handler)

class PositionSync:
    def __init__(self):
        self.binance = BinanceClient(BINANCE_API_KEY, BINANCE_SECRET_KEY)
        gate_config = Configuration(key=GATE_API_KEY, secret=GATE_SECRET_KEY)
        self.gate_client = ApiClient(gate_config)
        self.gate = FuturesApi(self.gate_client)
        self._initialized = False
        self._price = None
        self.last_binance_net = None  # 上一次币安净持仓（None=首次运行）
        self._alert_log = '/tmp/position_alerts.log'
        self._count_file = '/tmp/position_count.txt'
        logger.info("初始化完成")
        self._init_gate()

    def _init_gate(self):
        if self._initialized:
            return
        try:
            self.gate.set_position_mode(settle="usdt", position_mode="single")
            logger.info("Gate.io 单向模式")
        except:
            pass
        try:
            self.gate.update_position_leverage(settle="usdt", contract=GATE_CONTRACT, leverage=LEVERAGE)
            logger.info(f"杠杆: {LEVERAGE}x")
        except:
            pass
        self._initialized = True

    def _get_price(self):
        """获取当前价格"""
        try:
            contracts = self.gate.list_futures_contracts(settle="usdt")
            btc = next(c for c in contracts if c.name == GATE_CONTRACT)
            self._price = float(btc.mark_price)
            return self._price
        except:
            return self._price if self._price else 0

    def _get_binance_position(self):
        """获取币安净持仓(BTC)和开仓价。
        异常或数据校验失败时返回 (None, None)，调用方必须跳过本轮同步。"""
        try:
            account = self.binance.futures_account()

            # ── Layer 2: 响应结构校验 ──
            if not isinstance(account, dict):
                logger.error(f"币安API响应类型异常: {type(account)}")
                return None, None
            if 'positions' not in account:
                logger.error("币安API响应缺少 positions 字段")
                return None, None
            positions = account['positions']
            if not isinstance(positions, list) or len(positions) == 0:
                logger.error(f"币安API positions 为空或非列表: {type(positions)}")
                return None, None

            # 查找 BTCUSDT
            btc_position = None
            for p in positions:
                if not isinstance(p, dict) or 'symbol' not in p:
                    continue
                if p['symbol'] == SYMBOL:
                    btc_position = p
                    break

            if btc_position is None:
                logger.error(f"币安API positions 中未找到 {SYMBOL}")
                return None, None
            if 'positionAmt' not in btc_position:
                logger.error("币安API BTCUSDT 仓位缺少 positionAmt 字段")
                return None, None

            amt = float(btc_position['positionAmt'])
            entry_price = float(btc_position.get('entryPrice', 0)) if amt != 0 else 0
            return amt, entry_price

        except Exception as e:
            logger.error(f"获取币安持仓异常: {e}")
            return None, None

    def _get_gate_net(self):
        """获取Gate净持仓(合约)"""
        try:
            positions = self.gate.list_positions(settle="usdt")
            for p in positions:
                if p.contract == GATE_CONTRACT:
                    return float(p.size)  # 正=多, 负=空
            return 0
        except Exception as e:
            logger.error(f"获取Gate持仓: {e}")
            return 0

    def _open_long(self, qty):
        """开多"""
        if qty <= 0:
            return
        qty_int = round(qty)
        if qty_int == 0:
            return
        try:
            price = self._get_price()
            order = FuturesOrder(contract=GATE_CONTRACT, size=str(qty_int), price=str(price), tif="ioc")
            result = self.gate.create_futures_order(settle="usdt", futures_order=order)
            logger.info(f"开多: {qty_int}合约 @ {price}")
            return result
        except Exception as e:
            logger.error(f"开多失败: {e}")
            return None

    def _open_short(self, qty):
        """开空"""
        if qty <= 0:
            return
        qty_int = round(qty)
        if qty_int == 0:
            return
        try:
            price = self._get_price()
            order = FuturesOrder(contract=GATE_CONTRACT, size=str(-qty_int), price=str(price), tif="ioc")
            result = self.gate.create_futures_order(settle="usdt", futures_order=order)
            logger.info(f"开空: {qty_int}合约 @ {price}")
            return result
        except Exception as e:
            logger.error(f"开空失败: {e}")
            return None

    def _close_all(self, current_contracts):
        """平掉所有仓位"""
        if abs(current_contracts) < 0.5:
            return  # 小于0.5合约视为0
        try:
            price = self._get_price()
            if current_contracts > 0:
                # 平多
                order = FuturesOrder(contract=GATE_CONTRACT, size=str(-int(current_contracts)), price=str(price), tif="ioc")
                self.gate.create_futures_order(settle="usdt", futures_order=order)
                logger.info(f"平多: {int(current_contracts)}合约")
            else:
                # 平空
                order = FuturesOrder(contract=GATE_CONTRACT, size=str(abs(int(current_contracts))), price=str(price), tif="ioc")
                self.gate.create_futures_order(settle="usdt", futures_order=order)
                logger.info(f"平空: {abs(int(current_contracts))}合约")
        except Exception as e:
            logger.error(f"平仓失败: {e}")

    def _get_position_count(self):
        """读取累计开仓次数"""
        try:
            with open(self._count_file, 'r') as f:
                return int(f.read().strip())
        except:
            return 0

    def _increment_position_count(self):
        """递增并返回累计开仓次数"""
        count = self._get_position_count() + 1
        with open(self._count_file, 'w') as f:
            f.write(str(count))
        return count

    def _write_notification(self, direction, qty_btc, total_btc, entry_price, cumulative_count, event_type):
        """写入通知信号文件（由外部监控进程负责推送）"""
        if direction == "long":
            direction_line = "方向: 🟢【做多-LONG】📈"
            qty_sign = "+"
        else:
            direction_line = "方向: 🔴【做空-SHORT】📉"
            qty_sign = "+"

        msg = (
            f"🚨 BTC{event_type}通知（累计{cumulative_count}仓）\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{direction_line}\n"
            f"杠杆: {LEVERAGE}x\n"
            f"数量: {qty_sign}{qty_btc} BTC（合计 {total_btc} BTC）\n"
            f"开仓价: ${entry_price:,.2f}\n"
            f"━━━━━━━━━━━━━━━━"
        )

        # 写入通知日志
        with open(self._alert_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

        # 写入信号文件（JSON，供外部监控进程读取）
        import json as _json
        signal = {
            "timestamp": time.time(),
            "direction": direction,
            "qty_btc": qty_btc,
            "total_btc": total_btc,
            "entry_price": entry_price,
            "cumulative_count": cumulative_count,
            "event_type": event_type,
            "message": msg
        }
        with open('/tmp/position_notify.json', 'w') as f:
            _json.dump(signal, f, ensure_ascii=False)

        # 也尝试 delivery-queue（当 Bot WS 恢复时自动送达）
        try:
            import uuid as _uuid
            queue_entry = {
                "channel": "wecom",
                "to": "LiuGang",
                "payloads": [{"text": msg}]
            }
            queue_path = f"/root/.openclaw/delivery-queue/{_uuid.uuid4()}.json"
            with open(queue_path, 'w') as f:
                _json.dump(queue_entry, f, ensure_ascii=False)
        except Exception:
            pass

        logger.info(f"📢 已写入开仓信号: {direction} {qty_btc}BTC(合计{total_btc}BTC) @ ${entry_price:,.2f}")

    def _check_and_notify(self, binance_net, entry_price):
        """检测仓位变化并发送通知。
        覆盖四种场景：新开仓 / 加仓 / 减仓 / 方向翻转"""
        if self.last_binance_net is None:
            # 首次运行，仅记录状态
            return

        prev = self.last_binance_net
        curr = binance_net
        prev_abs = abs(prev)
        curr_abs = abs(curr)

        # 场景1: 从 0 → 非0 → 新开仓
        if prev == 0 and curr != 0:
            direction = "long" if curr > 0 else "short"
            count = self._increment_position_count()
            self._write_notification(direction, curr_abs, curr_abs, entry_price, count, "开仓")

        # 场景2: 方向翻转（先平后反向开）
        elif prev != 0 and curr != 0 and ((prev > 0 and curr < 0) or (prev < 0 and curr > 0)):
            direction = "long" if curr > 0 else "short"
            count = self._increment_position_count()
            self._write_notification(direction, curr_abs, curr_abs, entry_price, count, "翻转开仓")

        # 场景3: 同向加仓（|curr| > |prev|）
        elif prev != 0 and curr != 0 and ((prev > 0 and curr > 0) or (prev < 0 and curr < 0)):
            if curr_abs > prev_abs and (curr_abs - prev_abs) >= 0.001:
                direction = "long" if curr > 0 else "short"
                added = round(curr_abs - prev_abs, 4)
                count = self._increment_position_count()
                self._write_notification(direction, added, curr_abs, entry_price, count, "加仓")

            # 场景4: 同向减仓（|curr| < |prev|）— 仅记录，不推送计数
            elif curr_abs < prev_abs and (prev_abs - curr_abs) >= 0.001:
                reduced = round(prev_abs - curr_abs, 4)
                side = "多" if curr > 0 else ("空" if curr < 0 else "平仓")
                logger.info(f"📉 仓位减少: {side} -{reduced}BTC, 剩余 {curr_abs}BTC")

    def sync_positions(self):
        self._init_gate()
        
        # 获取净持仓和开仓价
        binance_net, entry_price = self._get_binance_position()
        if binance_net is None:
            logger.warning("币安持仓数据不可靠，跳过本轮同步")
            return  # Layer 1: 异常时拒绝行动
        gate_net = self._get_gate_net()
        
        # ── 仓位变化检测与通知 ──
        self._check_and_notify(binance_net, entry_price)
        # 更新上次仓位（仅当仓位确实变化时）
        if self.last_binance_net is None or self.last_binance_net != binance_net:
            self.last_binance_net = binance_net
        
        # 转换目标合约数
        target_contracts = round(binance_net * CONTRACT_RATIO)
        
        binance_side = "多" if binance_net > 0 else ("空" if binance_net < 0 else "无")
        gate_side = "多" if gate_net > 0 else ("空" if gate_net < 0 else "无")
        target_side = "多" if target_contracts > 0 else ("空" if target_contracts < 0 else "无")
        
        logger.info(f"币安净:{binance_net}BTC({binance_side}) | Gate净:{gate_net}合约({gate_side}) | 目标:{target_contracts}合约({target_side})")
        binance_log.info(f"币安 {binance_side}:{abs(binance_net)}BTC -> Gate {target_side}:{abs(target_contracts)}合约")
        
        # 计算差异
        diff = target_contracts - gate_net
        
        if abs(diff) < 0.5:
            # 差异小于0.5，跳过
            pass
        elif diff > 0:
            # 需要增加多头
            self._open_long(diff)
        elif diff < 0:
            # 需要减少多头或转向
            if gate_net > 0:
                self._close_all(gate_net)
            elif gate_net < 0:
                self._close_all(gate_net)
            # 只在目标不为零时重新开仓
            if target_contracts > 0:
                self._open_long(target_contracts)
            elif target_contracts < 0:
                self._open_short(abs(target_contracts))

    def run(self):
        logger.info("开始监控币安 -> Gate.io 仓位同步")
        logger.info(f"每2秒扫描 | 杠杆: {LEVERAGE}x | 同步净方向 | 逐仓模式")
        
        while True:
            try:
                self.sync_positions()
            except Exception as e:
                logger.error(f"同步出错: {e}")
            time.sleep(2)

if __name__ == "__main__":
    syncer = PositionSync()
    syncer.run()