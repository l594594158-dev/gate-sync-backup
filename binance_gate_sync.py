#!/usr/bin/env python3
"""
币安 -> Gate.io 仓位同步监控
同步净方向（多空相抵后的方向）
逐仓模式
"""

import time
import logging
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
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

    def _get_binance_net(self):
        """获取币安净持仓(BTC)"""
        try:
            account = self.binance.futures_account()
            net_btc = 0
            for p in account['positions']:
                if p['symbol'] == SYMBOL:
                    net_btc += float(p['positionAmt'])  # 正=多, 负=空
            return net_btc
        except Exception as e:
            logger.error(f"获取币安持仓: {e}")
            return 0

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

    def sync_positions(self):
        self._init_gate()
        
        # 获取净持仓
        binance_net = self._get_binance_net()  # BTC (正=多, 负=空)
        gate_net = self._get_gate_net()        # 合约 (正=多, 负=空)
        
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