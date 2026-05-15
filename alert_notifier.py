#!/usr/bin/env python3
"""
告警通知进程 (alert_notifier) v15
架构：sync 写 JSONL → notifier 读 → 两步投递
  1. 实时：写标准 delivery-queue 条目到 ~/.openclaw/delivery-queue/
     gateway 重启时自动恢复投递（recoverPendingDeliveries）
  2. 标记：记录 pending 标记文件供 AI session 兜底实时推送

三进程（sync / notifier / watchdog）走 supervisor 托管。
"""

import json
import logging
import os
import time
import uuid

PID_FILE = "/tmp/notifier.pid"
QUEUE_FILE = "/tmp/alert_queue.jsonl"
CURSOR_FILE = "/tmp/alert_cursor.txt"
DEAD_LETTER_FILE = "/tmp/notifier_deadletter.jsonl"
LOG_FILE = "/tmp/notifier.log"
PENDING_FLAG_FILE = "/tmp/pending_alert.flag"
DELIVERY_QUEUE_DIR = os.path.expanduser("~/.openclaw/delivery-queue")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [notifier] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def write_pid():
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def read_cursor() -> int:
    try:
        with open(CURSOR_FILE, 'r') as f:
            return int(f.read().strip())
    except:
        return 0


def write_cursor(line_no: int):
    with open(CURSOR_FILE, 'w') as f:
        f.write(str(line_no))


def dead_letter(record: dict, error: str):
    record['_error'] = error
    record['_dead_at'] = time.time()
    with open(DEAD_LETTER_FILE, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def write_delivery_entry(record: dict) -> str:
    """
    将告警转为标准 delivery-queue 条目。
    返回 entry_id (UUID)。
    格式匹配 openclaw delivery-queue 协议，确保 gateway 重启时可恢复。
    """
    msg_text = record.get('message', '')
    entry_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)

    entry = {
        "id": entry_id,
        "enqueuedAt": now_ms,
        "channel": "wecom",
        "to": "LiuGang",
        "accountId": "default",
        "payloads": [
            {
                "text": msg_text,
                "replyToTag": False,
                "replyToCurrent": False,
                "audioAsVoice": False
            }
        ],
        "gifPlayback": False,
        "forceDocument": False,
        "silent": False,
        "retryCount": 0
    }

    # 原子写入：先写 .tmp 再 rename，匹配 enqueueDelivery 行为
    os.makedirs(DELIVERY_QUEUE_DIR, exist_ok=True)
    tmp_path = os.path.join(DELIVERY_QUEUE_DIR, f"{entry_id}.{os.getpid()}.tmp")
    final_path = os.path.join(DELIVERY_QUEUE_DIR, f"{entry_id}.json")

    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    os.rename(tmp_path, final_path)

    return entry_id


def process_queue():
    if not os.path.exists(QUEUE_FILE):
        return

    cursor = read_cursor()
    try:
        with open(QUEUE_FILE, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"读取队列文件失败: {e}")
        return

    total_lines = len(lines)
    if cursor >= total_lines:
        return

    new_lines = lines[cursor:]
    logger.info(f"发现 {len(new_lines)} 条待处理 (游标 {cursor}/{total_lines})")

    for i, line in enumerate(new_lines):
        line = line.strip()
        if not line:
            cursor += 1
            write_cursor(cursor)
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"跳过无效 JSON 行 {cursor + i + 1}")
            cursor += 1
            write_cursor(cursor)
            continue

        msg_text = record.get('message', '')
        event_type = record.get('event_type', 'unknown')
        cumulative = record.get('cumulative_count', 0)

        if not msg_text:
            cursor += 1
            write_cursor(cursor)
            continue

        # 1. 写入 delivery-queue（gateway 重启恢复）
        try:
            entry_id = write_delivery_entry(record)
            logger.info(f"📝 [{event_type} #{cumulative}] delivery-queue 入队: {entry_id}")
        except Exception as e:
            logger.error(f"❌ delivery-queue 写入失败: {e}")
            dead_letter(record, f"delivery-queue write error: {e}")
            cursor += 1
            write_cursor(cursor)
            continue

        # 2. 写 pending flag（AI session 兜底实时推送）
        try:
            with open(PENDING_FLAG_FILE, 'w') as f:
                f.write(f"{entry_id}\n{msg_text}")
        except:
            pass

        logger.info(f"✅ [{event_type} #{cumulative}] 投递完成 → {entry_id}")

        cursor += 1
        write_cursor(cursor)

    if len(new_lines) > 0:
        logger.info(f"批次完成, 游标推进至 {cursor}/{total_lines}")


def run():
    write_pid()
    logger.info(f"告警通知进程 v15 启动 (PID: {os.getpid()})")
    logger.info(f"监听队列: {QUEUE_FILE}")
    logger.info(f"投递目标: {DELIVERY_QUEUE_DIR}")

    while True:
        try:
            process_queue()
        except Exception as e:
            logger.error(f"处理队列异常: {e}")
        time.sleep(2)


if __name__ == "__main__":
    run()
