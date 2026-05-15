#!/usr/bin/env python3
"""
gate-sync 看门狗进程 (gate_sync_watchdog) v15
监控 sync + notifier 双进程存活/日志活性/队列积压。
异常自动重启（调用 start_gate_sync.sh）。
"""

import logging
import os
import subprocess
import time

PID_FILE = "/tmp/watchdog.pid"
LOG_FILE = "/tmp/watchdog.log"
SYNC_LOG = "/tmp/sync.log"
NOTIFIER_LOG = "/tmp/notifier.log"
QUEUE_FILE = "/tmp/alert_queue.jsonl"
CURSOR_FILE = "/tmp/alert_cursor.txt"
STARTER = os.path.expanduser("~/.openclaw/start_gate_sync.sh")

CHECK_INTERVAL = 300  # 5 分钟

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [watchdog] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def write_pid():
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def pid_alive(pid_file: str) -> bool:
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except:
        return False


def file_age_seconds(path: str) -> float:
    try:
        return time.time() - os.path.getmtime(path)
    except:
        return float('inf')


def count_lines(path: str) -> int:
    try:
        with open(path, 'r') as f:
            return sum(1 for _ in f)
    except:
        return -1


def queue_depth() -> int:
    """未消费的队列行数"""
    try:
        cursor = 0
        try:
            with open(CURSOR_FILE, 'r') as f:
                cursor = int(f.read().strip())
        except:
            pass
        total = count_lines(QUEUE_FILE)
        if total < 0:
            return -1
        return max(0, total - cursor)
    except:
        return -1


def check_and_restart() -> bool:
    """检查并重启。返回 True 表示执行了重启。"""
    issues = []

    # 1. sync 存活
    if not pid_alive("/tmp/sync.pid"):
        issues.append("sync 进程不存在")
    else:
        # sync 日志活性
        if file_age_seconds(SYNC_LOG) > CHECK_INTERVAL * 2:
            issues.append(f"sync 日志僵死 ({file_age_seconds(SYNC_LOG):.0f}s)")

    # 2. notifier 存活
    if not pid_alive("/tmp/notifier.pid"):
        issues.append("notifier 进程不存在")
    else:
        if file_age_seconds(NOTIFIER_LOG) > CHECK_INTERVAL * 2:
            issues.append(f"notifier 日志僵死 ({file_age_seconds(NOTIFIER_LOG):.0f}s)")

    # 3. 队列积压检查
    depth = queue_depth()
    if depth > 100:
        issues.append(f"队列积压严重: {depth} 条")

    if not issues:
        logger.info(f"健康检查通过 ✅ sync+notifier 均存活")
        return False

    logger.error(f"健康检查失败: {'; '.join(issues)}，触发重启...")
    try:
        result = subprocess.run(
            ["bash", STARTER, "restart"],
            capture_output=True, text=True, timeout=30
        )
        logger.info(f"重启结果: {result.stdout.strip()}")
        if result.returncode != 0:
            logger.error(f"重启失败: {result.stderr.strip()}")
    except Exception as e:
        logger.error(f"重启异常: {e}")
    return True


def run():
    write_pid()
    logger.info(f"看门狗进程 v15 启动 (PID: {os.getpid()})")
    logger.info(f"检查间隔: {CHECK_INTERVAL}s")

    while True:
        check_and_restart()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
