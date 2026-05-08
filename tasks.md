# 运行任务记忆

## 币安↔Gate 仓位同步项目

### 同步任务
- 脚本: `/root/.openclaw/binance_gate_sync.py`
- PID: 1028805
- 状态: 运行中
- 功能: 币安→Gate 仓位同步，20x逐仓，净方向同步
- 转换比例: 币安1 BTC = 10000 Gate合约

### 健康检查任务
- 脚本: `/root/.openclaw/health_check.py`
- PID: 1032897
- 状态: 运行中
- 功能: 每5分钟检查同步任务，异常自动重启

### 日志位置
- `/tmp/sync.log` - 同步任务日志
- `/tmp/health_check.log` - 健康检查日志

### 启动命令
```bash
cd ~/.openclaw && python3 binance_gate_sync.py
cd ~/.openclaw && python3 health_check.py
```

