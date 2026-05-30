import json
import os
import time
import urllib.request
from datetime import datetime, timedelta
from collections import deque

STOCK_CODE = "sz300424"
STOCK_NAME = "航新科技"
OUTPUT_DIR = os.path.expanduser("~/.claude/stock_data")
STATE_FILE = os.path.join(OUTPUT_DIR, "monitor_state.json")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "hangxin_history.jsonl")
ALERT_LOG = os.path.join(OUTPUT_DIR, "escape_alerts.log")

FETCH_INTERVAL = 60  # 秒
SAMPLES_MAXLEN = 30  # 保留最近30个采样点（约30分钟）

def fetch_sina_realtime(code):
    url = f"https://hq.sinajs.cn/list={code}"
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0'
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read().decode('gbk')
        prefix = f'var hq_str_{code}="'
        if prefix in data:
            content = data.split(prefix)[1].split('";')[0]
            fields = content.split(',')
            if len(fields) >= 33:
                return {
                    "name": fields[0],
                    "open": float(fields[1]),
                    "previous_close": float(fields[2]),
                    "current": float(fields[3]),
                    "high": float(fields[4]),
                    "low": float(fields[5]),
                    "volume": int(fields[8]),
                    "amount": float(fields[9]),
                    "date": fields[30],
                    "time": fields[31],
                }
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Fetch error: {e}")
    return None

def load_state():
    """加载监控状态（14:30快照、已发警报记录等）"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "alerted_escape_types": [],  # 今日已发的出逃信号类型（去重）
        "snapshots": {},             # 关键时刻快照
        "last_amount": 0,            # 上次成交额（用于估算区间成交）
    }

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_history(days=5):
    """加载最近N天的历史数据，计算均值"""
    if not os.path.exists(HISTORY_FILE):
        return []
    records = []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                if r.get("date", "") >= cutoff:
                    records.append(r)
            except:
                continue
    return records

def calc_avg_amount(history_records):
    """计算前5日全天成交额均值"""
    daily_amount = {}
    for r in history_records:
        d = r.get("date")
        if d:
            daily_amount[d] = max(daily_amount.get(d, 0), r.get("amount", 0))
    amounts = list(daily_amount.values())
    if len(amounts) >= 2:
        return sum(amounts) / len(amounts)
    return 0

def detect_escape(data, state, samples, avg_amount_5d):
    """
    出逃行情多维度监测模型
    返回: (alerts列表, 综合危险评分0-100)
    """
    alerts = []
    score = 0
    now = datetime.now()
    current = data["current"]
    open_p = data["open"]
    high = data["high"]
    low = data["low"]
    prev = data["previous_close"]
    amount = data["amount"]
    change_pct = (current - prev) / prev * 100 if prev > 0 else 0

    # ========== 维度1: 放量下跌（资金出逃最核心的信号）==========
    if change_pct < -1.5 and avg_amount_5d > 0:
        ratio = amount / avg_amount_5d
        # 动态判断：盘中时间越晚，累计成交额应该越高
        hm = now.hour * 100 + now.minute
        progress = 0.3  # 默认假设已交易30%时间
        if 930 <= hm <= 1130:
            progress = (hm - 930) / 200 * 0.4  # 上午占40%
        elif 1300 <= hm <= 1500:
            progress = 0.4 + (hm - 1300) / 200 * 0.6  # 下午占60%
        expected_ratio = progress
        if ratio > expected_ratio * 2.0:  # 实际成交额是预期进度的2倍以上
            alerts.append(f"出逃-巨量下跌: 成交额已达前5日均值{ratio:.1f}倍")
            score += 35
        elif ratio > expected_ratio * 1.5:
            alerts.append(f"出逃-明显放量: 成交额为前5日均值{ratio:.1f}倍")
            score += 20

    # ========== 维度2: 冲高回落（诱多后闷杀）==========
    if high > open_p * 1.005:  # 盘中曾有过冲高
        drop_from_high = (high - current) / high * 100
        if drop_from_high > 3 and change_pct < -1.5:
            alerts.append(f"出逃-冲高回落: 从高点{high}回撤{drop_from_high:.1f}%")
            score += 25
        elif drop_from_high > 5:
            alerts.append(f"出逃-深度回落: 从高点{high}暴跌{drop_from_high:.1f}%")
            score += 30

    # ========== 维度3: 高开低走（开盘诱多，全天无抵抗）==========
    if open_p > prev * 1.005:  # 高开
        drop_from_open = (open_p - current) / open_p * 100
        if drop_from_open > 4:
            alerts.append(f"出逃-高开闷杀: 高开{((open_p/prev-1)*100):.1f}%后跌{drop_from_open:.1f}%")
            score += 30
        elif drop_from_open > 2.5 and change_pct < -1:
            alerts.append(f"出逃-高开低走: 开盘即巅峰，无反弹")
            score += 18

    # ========== 维度4: 尾盘急杀（14:30后资金抢跑）==========
    if (now.hour == 14 and now.minute >= 30) or now.hour == 15:
        snap_key = f"{data['date']}_1430"
        if snap_key in state["snapshots"]:
            price_1430 = state["snapshots"][snap_key]["current"]
            amount_1430 = state["snapshots"][snap_key]["amount"]
            drop_since_1430 = (price_1430 - current) / price_1430 * 100
            amount_after_1430 = amount - amount_1430
            if drop_since_1430 > 1.5:
                alerts.append(f"出逃-尾盘跳水: 14:30后急杀{drop_since_1430:.1f}%")
                score += 22
            if amount_after_1430 > avg_amount_5d * 0.25:
                alerts.append(f"出逃-尾盘放量: 14:30后成交{amount_after_1430/1e8:.1f}亿")
                score += 15

    # ========== 维度5: 持续创新低（无反弹，空头完全主导）==========
    if len(samples) >= 5:
        recent_lows = [s["current"] for s in list(samples)[-5:]]
        # 连续5个采样点，每个都比前一个低（或持平）
        if all(recent_lows[i] <= recent_lows[i-1] for i in range(1, len(recent_lows))):
            if change_pct < -2:
                alerts.append("出逃-持续杀跌: 连续5分钟无反弹，空头主导")
                score += 20

    # ========== 维度6: 跌破关键心理位 ==========
    if current < open_p and open_p > prev:  # 从高开跌破开盘价
        alerts.append(f"出逃-翻绿: 跌破开盘价{open_p}")
        score += 10
    if current < prev and open_p > prev * 1.01:  # 高开翻绿
        alerts.append("出逃-高开翻绿: 开盘诱多后翻绿")
        score += 12

    # ========== 综合评级 ==========
    if score >= 60:
        alerts.insert(0, f"【严重出逃信号】综合评分{score}/100")
    elif score >= 40:
        alerts.insert(0, f"【明显出逃信号】综合评分{score}/100")
    elif score >= 20:
        alerts.insert(0, f"【轻度出逃信号】综合评分{score}/100")

    return alerts, score

def is_trading_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hour, minute = now.hour, now.minute
    hm = hour * 100 + minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    state = load_state()
    samples = deque(maxlen=SAMPLES_MAXLEN)

    # 每日重置
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_date") != today:
        state["alerted_escape_types"] = []
        state["snapshots"] = {}
        state["last_date"] = today
        print(f"[{datetime.now().isoformat()}] 新交易日，状态已重置")

    history = load_history(days=7)  # 多取几天确保有足够数据
    avg_amount_5d = calc_avg_amount(history)

    print(f"[{datetime.now().isoformat()}] 出逃监控系统启动: {STOCK_NAME}({STOCK_CODE.replace('sh','').replace('sz','')})")
    print(f"前5日成交额均值: {avg_amount_5d/1e8:.2f}亿" if avg_amount_5d > 0 else "历史数据不足，放量判断暂不可用")
    print(f"抓取间隔: {FETCH_INTERVAL}秒 | 按 Ctrl+C 停止\n")

    last_time = None

    while True:
        if not is_trading_time():
            time.sleep(300)
            continue

        data = fetch_sina_realtime(STOCK_CODE)
        if not data:
            time.sleep(FETCH_INTERVAL)
            continue

        if data["time"] == last_time:
            time.sleep(FETCH_INTERVAL)
            continue
        last_time = data["time"]

        # 保存14:30快照
        now = datetime.now()
        if now.hour == 14 and now.minute == 30:
            snap_key = f"{data['date']}_1430"
            if snap_key not in state["snapshots"]:
                state["snapshots"][snap_key] = {
                    "current": data["current"],
                    "amount": data["amount"],
                    "time": data["time"]
                }
                save_state(state)

        samples.append({
            "time": data["time"],
            "current": data["current"],
            "amount": data["amount"]
        })

        alerts, score = detect_escape(data, state, samples, avg_amount_5d)

        # 去重：同类型的出逃信号今天只报一次
        new_alerts = []
        for a in alerts:
            # 提取信号类型（冒号前的部分）
            sig_type = a.split(":")[0] if ":" in a else a
            if sig_type not in state["alerted_escape_types"]:
                state["alerted_escape_types"].append(sig_type)
                new_alerts.append(a)

        if new_alerts:
            with open(ALERT_LOG, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now().isoformat()}] {data['time']} {data['current']} {new_alerts}\n")
            print(f"[{datetime.now().isoformat()}] ALERT {data['time']} {data['current']} {new_alerts}")
        else:
            print(f"[{datetime.now().isoformat()}] {data['time']} {STOCK_NAME} {data['current']} ({(data['current']/data['previous_close']-1)*100:.2f}%) 出逃评分:{score}")

        save_state(state)
        time.sleep(FETCH_INTERVAL)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n监控已停止")
