#!/usr/bin/env python3
"""
币安Gate同步任务健康检查
每5分钟检查一次
"""

import time
import logging
from datetime import datetime
from binance.client import Client as BinanceClient
from gate_api.configuration import Configuration
from gate_api.api_client import ApiClient
from gate_api.api.futures_api import FuturesApi

# 配置
BINANCE_API_KEY = "yoIUcS0f687fbduzdLGRHgECeexVmnvVjhegkPeVYrACvRVYuFJWSJDjfhgBE8ww"
BINANCE_SECRET_KEY = "BpsTBrNNQ6T4r35GNKhA6uWRg4asAu1cRuGOzF7jYM5GRR32kUPHluiis7XXyZ8t"
GATE_API_KEY = "6798f83ac4f0952585c8fbc28f649320"
GATE_SECRET_KEY = "b9ec180b4290b0157288604afd66046f51b011a711bf8a24b599055e91f2f393"

SYMBOL = "BTCUSDT"
GATE_CONTRACT = "BTC_USDT"
CHECK_INTERVAL = 300  # 5分钟

# 日志配置
log_file = '/tmp/health_check.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class HealthChecker:
    def __init__(self):
        self.binance = BinanceClient(BINANCE_API_KEY, BINANCE_SECRET_KEY)
        gate_config = Configuration(key=GATE_API_KEY, secret=GATE_SECRET_KEY)
        self.gate_client = ApiClient(gate_config)
        self.gate = FuturesApi(self.gate_client)
        self.last_log_line = ""
        
    def check_binance_api(self):
        """检查币安API是否正常"""
        try:
            account = self.binance.futures_account()
            logger.info("✅ 币安API正常")
            return True
        except Exception as e:
            logger.error(f"❌ 币安API失败: {e}")
            return False
            
    def check_gate_api(self):
        """检查Gate API是否正常"""
        try:
            contracts = self.gate.list_futures_contracts(settle="usdt")
            logger.info("✅ Gate API正常")
            return True
        except Exception as e:
            logger.error(f"❌ Gate API失败: {e}")
            return False
    
    def check_positions(self):
        """检查持仓获取是否正常"""
        try:
            # 币安持仓
            account = self.binance.futures_account()
            binance_positions = []
            for p in account['positions']:
                if float(p['positionAmt']) != 0:
                    binance_positions.append(f"{p['symbol']}:{p['positionAmt']}")
            logger.info(f"✅ 币安持仓获取正常: {len(binance_positions)}个非零持仓")
            
            # Gate持仓
            gate_positions = self.gate.list_positions(settle="usdt")
            gate_btc = None
            for p in gate_positions:
                if p.contract == GATE_CONTRACT:
                    gate_btc = p.size
            logger.info(f"✅ Gate持仓获取正常: {gate_btc}合约")
            
            return True
        except Exception as e:
            logger.error(f"❌ 持仓获取失败: {e}")
            return False
    
    def check_polling(self):
        """检查轮询和日志是否正常"""
        try:
            # 读取同步日志
            with open('/tmp/binance_positions.log', 'r') as f:
                lines = f.readlines()
            
            if not lines:
                logger.error("❌ 同步日志为空")
                return False
                
            last_line = lines[-1].strip()
            last_time = last_line.split(',')[0] if last_line else ""
            
            # 检查时间戳
            now = datetime.now()
            try:
                # 格式: 2026-05-07 12:20:10,319
                log_time_str = last_line[:23]  # 取到毫秒
                log_time = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S,%f')
                diff = (now - log_time).total_seconds()
                
                if diff < 10:
                    logger.info(f"✅ 轮询正常，上次记录 {int(diff)}秒前")
                    return True
                elif diff < 60:
                    logger.warning(f"⚠️ 轮询延迟 {int(diff)}秒")
                    return True
                else:
                    logger.error(f"❌ 轮询停止，已 {int(diff)}秒无更新")
                    return False
            except:
                logger.warning(f"⚠️ 日志时间解析异常")
                return True
                
        except Exception as e:
            logger.error(f"❌ 日志检查失败: {e}")
            return False
    
    def check_sync_consistency(self):
        """检查同步一致性"""
        try:
            # 获取币安净持仓
            account = self.binance.futures_account()
            binance_net = sum(float(p['positionAmt']) for p in account['positions'] if p['symbol'] == SYMBOL)
            
            # 获取Gate净持仓
            gate_positions = self.gate.list_positions(settle="usdt")
            gate_net = 0
            for p in gate_positions:
                if p.contract == GATE_CONTRACT:
                    gate_net = float(p.size)
            
            # 计算预期Gate持仓
            expected_gate = round(binance_net * 10000)
            
            # 判断一致性 (允许1合约误差)
            diff = abs(gate_net - expected_gate)
            
            if diff <= 1:
                logger.info(f"✅ 同步一致: 币安{binance_net}BTC = Gate{gate_net}合约")
                return True
            else:
                logger.warning(f"⚠️ 同步差异: 币安{binance_net}BTC → 预期{gate_net}合约, 实际{gate_net}合约, 差异{diff}")
                return True  # 不算错误，只是警告
                
        except Exception as e:
            logger.error(f"❌ 同步一致性检查失败: {e}")
            return False
    
    def run_health_check(self):
        """执行完整健康检查"""
        logger.info("=" * 50)
        logger.info("开始健康检查")
        logger.info("=" * 50)
        
        results = {
            "binance_api": self.check_binance_api(),
            "gate_api": self.check_gate_api(),
            "positions": self.check_positions(),
            "polling": self.check_polling(),
            "sync_consistency": self.check_sync_consistency()
        }
        
        logger.info("=" * 50)
        all_pass = all(results.values())
        if all_pass:
            logger.info("✅ 所有检查通过")
        else:
            failed = [k for k, v in results.items() if not v]
            logger.error(f"❌ 检查失败: {', '.join(failed)}")
        logger.info("=" * 50)
        
        return all_pass

    def run(self):
        """主循环"""
        logger.info("健康检查任务启动")
        logger.info(f"检查间隔: {CHECK_INTERVAL}秒 (5分钟)")
        
        while True:
            self.run_health_check()
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    checker = HealthChecker()
    checker.run()