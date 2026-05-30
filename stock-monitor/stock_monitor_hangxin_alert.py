import json
import os
import time
import urllib.request
import subprocess
from datetime import datetime, timedelta
from collections import deque

STOCK_CODE = "sz300424"
STOCK_NAME = "航新科技"
OUTPUT_DIR = os.path.expanduser("~/.claude/stock_data")
STATE_FILE = os.path.join(OUTPUT_DIR, "monitor_state.json")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "hangxin_history.jsonl")
ALERT_LOG = os.path.join(OUTPUT_DIR, "escape_alerts.log")

FETCH_INTERVAL = 60
SAMPLES_MAXLEN = 30

# ---- 备选数据源（腾讯） ----
def fetch_tencent_realtime(code):
    """腾讯行情接口备选"""
    tcode = code.replace("sz", "sz").replace("sh", "sh")
    url = f"https://qt.gtimg.cn/q={tcode}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('gbk')
        # 格式: v_sz300424="...51个字段用~分隔"
        if '="' in raw:
            content = raw.split('="')[1].split('";')[0]
            fields = content.split('~')
            if len(fields) >= 40:
                return {
                    "name": fields[1],
                    "current": float(fields[3]) if fields[3] else 0,
                    "previous_close": float(fields[4]) if fields[4] else 0,
                    "open": float(fields[5]) if fields[5] else 0,
                    "high": float(fields[33]) if fields[33] else 0,
                    "low": float(fields[34]) if fields[34] else 0,
                    "volume": int(fields[6]) if fields[6] else 0,
                    "amount": float(fields[37]) if fields[37] else 0,
                    "date": fields[43] if fields[43] else "",
                    "time": f"{fields[44][:2]}:{fields[44][2:4]}:{fields[44][4:]}" if len(fields[44]) >= 6 else "",
                }
    except Exception as e:
        print(f"[腾讯接口] 失败: {e}")
    return None


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
        print(f"[新浪接口] 失败: {e}")
    return None


def fetch_realtime(code):
    """主接口失败时自动切换到备选"""
    data = fetch_sina_realtime(code)
    if data:
        return data
    print("[监控] 新浪接口失败，切换至腾讯接口")
    data = fetch_tencent_realtime(code)
    if data:
        return data
    print("[监控] 所有接口均失败")
    return None


# ---- 板块数据（新增 P0 改进） ----
SECTOR_MAP = {
    "低空经济": "BK1165",   # 东方财富概念板块代码
    "航天军工": "BK0488",
    "大飞机": "BK0730",
}

def fetch_sector_change(code="BK1165"):
    """获取板块指数涨跌幅（东方财富）"""
    secid = f"0.{code}" if not code.startswith("1.") else code
    url = (
        f"https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&invt=2&fields=f2,f3,f12,f14&secids={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        diff = data.get("data", {}).get("diff", [])
        if diff and diff[0].get("f3") is not None:
            return diff[0].get("f3")
    except Exception as e:
        print(f"[板块] 获取失败: {e}")
    return None


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "alerted_escape_types": [],
        "snapshots": {},
        "last_amount": 0,
        "pre_alert": {},          # 预警状态（二次确认用）
    }

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_history(days=5):
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

def fetch_eastmoney_fund_flow(stock_code="300424"):
    secid = f"0.{stock_code}" if stock_code.startswith("3") or stock_code.startswith("0") else f"1.{stock_code}"
    url = (
        f"https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&invt=2&fields=f2,f3,f12,f14,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124"
        f"&secids={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        diff = data.get("data", {}).get("diff", [])
        if diff:
            d = diff[0]
            return {
                "current": d.get("f2"),
                "change_pct": d.get("f3"),
                "main_net": d.get("f62", 0),
                "super_net": d.get("f66", 0),
                "super_ratio": d.get("f69", 0),
                "large_net": d.get("f72", 0),
                "large_ratio": d.get("f75", 0),
                "mid_net": d.get("f78", 0),
                "mid_ratio": d.get("f81", 0),
                "small_net": d.get("f84", 0),
                "small_ratio": d.get("f87", 0),
                "timestamp": d.get("f124"),
            }
    except Exception as e:
        print(f"[资金流] 失败: {e}")
    return None


def fetch_eastmoney_fund_history(stock_code="300424"):
    secid = f"0.{stock_code}" if stock_code.startswith("3") or stock_code.startswith("0") else f"1.{stock_code}"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2&volt=2"
        f"&fields=f178&secid={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data.get("data", {}).get("f178", "[]")
        return json.loads(raw)
    except Exception as e:
        print(f"[资金历史] 失败: {e}")
    return []


def calc_avg_amount(history_records):
    daily_amount = {}
    for r in history_records:
        d = r.get("date")
        if d:
            daily_amount[d] = max(daily_amount.get(d, 0), r.get("amount", 0))
    amounts = list(daily_amount.values())
    if len(amounts) >= 2:
        return sum(amounts) / len(amounts)
    return 0

CC_CONNECT_PATH = r'C:\Users\D1405\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\node\cc-connect.cmd'

def popup(title, message, critical=False):
    try:
        label = "[严重警报]" if critical else "[监控提醒]"
        full_msg = f"{label} {title}\n{'='*20}\n{message}"

        cmd = [CC_CONNECT_PATH, 'send', '--stdin']
        if not os.path.exists(CC_CONNECT_PATH):
            cmd = ['cc-connect', 'send', '--stdin']

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8'
        )
        proc.communicate(input=full_msg, timeout=15)
    except Exception as e:
        print(f"[微信] 发送失败: {e}")


def detect_escape(data, state, samples, avg_amount_5d,
                  fund_data=None, fund_history=None,
                  sector_chg=None):
    """
    增强版出逃检测（P0+P1 改进）
    - sector_chg: 关联板块涨跌幅（用于过滤系统性下跌）
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

    # ====== P0 改进：板块对比过滤 ======
    sector_adjust = 0
    if sector_chg is not None and change_pct < 0:
        relative_chg = change_pct - sector_chg  # 个股相对板块的超额跌幅
        if sector_chg < -2:
            # 板块大跌 >2%：系统性风险，整体降低出逃评分
            sector_adjust -= 10
            alerts.append(f"板块背景: 关联板块大跌{sector_chg:.1f}%，系统性风险主导")
        if abs(relative_chg) < 2:
            # 个股跌幅与板块接近（差异<2%）：跟跌而非出逃
            sector_adjust -= 10
            alerts.append(f"板块关联: 个股跌幅({change_pct:.1f}%)与板块({sector_chg:.1f}%)接近，偏跟跌")

    score += sector_adjust
    score = max(score, 0)

    # ====== 维度1: 放量下跌 ======
    if change_pct < -1.5 and avg_amount_5d > 0:
        ratio = amount / avg_amount_5d
        hm = now.hour * 100 + now.minute
        progress = 0.3
        if 930 <= hm <= 1130:
            progress = (hm - 930) / 200 * 0.4
        elif 1300 <= hm <= 1500:
            progress = 0.4 + (hm - 1300) / 200 * 0.6
        expected_ratio = progress
        if ratio > expected_ratio * 2.0:
            alerts.append(f"出逃-巨量下跌: 成交额已达前5日均值{ratio:.1f}倍")
            score += 35
        elif ratio > expected_ratio * 1.5:
            alerts.append(f"出逃-明显放量: 成交额为前5日均值{ratio:.1f}倍")
            score += 20

    # ====== 维度2: 冲高回落 ======
    if high > open_p * 1.005:
        drop_from_high = (high - current) / high * 100
        if drop_from_high > 3 and change_pct < -1.5:
            alerts.append(f"出逃-冲高回落: 从高点{high}回撤{drop_from_high:.1f}%")
            score += 25
        elif drop_from_high > 5:
            alerts.append(f"出逃-深度回落: 从高点{high}暴跌{drop_from_high:.1f}%")
            score += 30

    # ====== 维度3: 高开低走 ======
    if open_p > prev * 1.005:
        drop_from_open = (open_p - current) / open_p * 100
        if drop_from_open > 4:
            alerts.append(f"出逃-高开闷杀: 高开{((open_p/prev-1)*100):.1f}%后跌{drop_from_open:.1f}%")
            score += 30
        elif drop_from_open > 2.5 and change_pct < -1:
            alerts.append(f"出逃-高开低走: 开盘即巅峰，无反弹")
            score += 18

    # ====== 维度4: 尾盘急杀 ======
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

    # ====== 维度5: 持续创新低（P1 二次确认增强） ======
    if len(samples) >= 5:
        recent_5 = list(samples)[-5:]
        recent_lows = [s["current"] for s in recent_5]
        if all(recent_lows[i] <= recent_lows[i-1] for i in range(1, len(recent_lows))):
            # P1 增强：检查连续下跌幅度
            drop_in_window = (recent_5[0]["current"] - recent_5[-1]["current"]) / recent_5[0]["current"] * 100
            if drop_in_window > 1.5:
                if change_pct < -2:
                    alerts.append(f"出逃-持续杀跌: 连续5分钟无反弹，累计跌{drop_in_window:.1f}%")
                    score += 25
                elif change_pct < -1:
                    alerts.append(f"预警-阴跌不止: 连续5分钟单边下行")
                    score += 15

    # ====== 维度6: 跌破关键心理位 ======
    if current < open_p and open_p > prev:
        alerts.append(f"出逃-翻绿: 跌破开盘价{open_p}")
        score += 10
    if current < prev and open_p > prev * 1.01:
        alerts.append("出逃-高开翻绿: 开盘诱多后翻绿")
        score += 12

    # ====== 维度7: 主力资金分析 ======
    if fund_data:
        main_net = fund_data.get("main_net", 0)
        super_net = fund_data.get("super_net", 0)
        large_net = fund_data.get("large_net", 0)
        small_net = fund_data.get("small_net", 0)

        if change_pct < -5 and main_net > 10000000:
            alerts.append(f"主力异动: 股价大跌{change_pct:.1f}%但主力净流入{main_net/1e4:.0f}万，疑似洗盘/换庄")
            score -= 15

        if main_net < -50000000:
            alerts.append(f"出逃-主力抛售: 主力净流出{abs(main_net)/1e4:.0f}万")
            score += 25
        elif main_net < -10000000:
            alerts.append(f"注意-主力流出: 主力净流出{abs(main_net)/1e4:.0f}万")
            score += 15

        if super_net < 0 and large_net < 0:
            alerts.append("出逃-机构一致卖出: 超大单和大单同步净流出")
            score += 20

        if main_net < -10000000 and small_net > 5000000:
            alerts.append(f"出逃-散户接盘: 主力卖出{abs(main_net)/1e4:.0f}万，散户买入{small_net/1e4:.0f}万")
            score += 15

        if small_net < -10000000 and main_net > 0:
            alerts.append(f"主力吸筹: 散户割肉卖出{abs(small_net)/1e4:.0f}万，主力顺势吸筹")
            score -= 10

    # ====== 维度8: 连续主力资金流向 ======
    if fund_history:
        recent_outflow = sum(1 for h in fund_history if h.get("mainNetAmt", 0) < 0)
        if len(fund_history) >= 3 and recent_outflow >= 3:
            if fund_data and fund_data.get("main_net", 0) < 0:
                alerts.append(f"出逃-主力连续流出: 近{len(fund_history)}日有{recent_outflow}日主力净流出，今日继续流出")
                score += 15
            elif fund_data and fund_data.get("main_net", 0) > 10000000:
                alerts.append(f"主力反转: 近{len(fund_history)}日有{recent_outflow}日流出，但今日主力逆势流入{fund_data['main_net']/1e4:.0f}万")
                score -= 10

    # ====== 综合评级 ======
    score = max(score, 0)
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

    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_date") != today:
        state["alerted_escape_types"] = []
        state["snapshots"] = {}
        state["pre_alert"] = {}
        state["last_date"] = today
        print(f"[{datetime.now().isoformat()}] 新交易日，状态已重置")

    history = load_history(days=7)
    avg_amount_5d = calc_avg_amount(history)
    fund_history = fetch_eastmoney_fund_history("300424")

    print(f"[{datetime.now().isoformat()}] 航新科技出逃监控启动（增强版 v2.1）")
    print(f"[{datetime.now().isoformat()}] 数据源: 新浪主用 / 腾讯备选")
    print(f"[{datetime.now().isoformat()}] 监控维度: 6D技术面 + 3D资金面 + 板块对比 + 二次确认")
    print(f"[{datetime.now().isoformat()}] 前5日成交额均值: {avg_amount_5d/1e8:.2f}亿" if avg_amount_5d > 0 else "历史数据不足")
    if fund_history:
        fund_history_str = [f"{h['date']}: {h['mainNetAmt']/1e4:.0f}万" for h in fund_history]
        print(f"[{datetime.now().isoformat()}] 近5日主力流向: {fund_history_str}")
    print(f"[{datetime.now().isoformat()}] 抓取间隔: {FETCH_INTERVAL}秒 | 微信推送: 异常时自动发送\n")

    last_time = None
    check_counter = 0

    while True:
        if not is_trading_time():
            time.sleep(300)
            continue

        # ====== 获取行情数据（主+备自动切换） ======
        data = fetch_realtime(STOCK_CODE)
        if not data:
            time.sleep(FETCH_INTERVAL)
            continue

        if data["time"] == last_time:
            time.sleep(FETCH_INTERVAL)
            continue
        last_time = data["time"]

        # ====== 获取板块数据（每5次采样取一次，减少请求） ======
        sector_chg = None
        if check_counter % 5 == 0:
            sector_chg = fetch_sector_change("BK1165")  # 低空经济板块
            if sector_chg is None:
                sector_chg = fetch_sector_change("BK0488")  # 军工板块备选
        check_counter += 1

        # ====== 获取主力资金数据 ======
        fund_data = fetch_eastmoney_fund_flow("300424")

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

        alerts, score = detect_escape(
            data, state, samples, avg_amount_5d,
            fund_data, fund_history, sector_chg
        )

        # ====== P1 改进：二次确认机制 ======
        new_alerts = []
        for a in alerts:
            sig_type = a.split(":")[0] if ":" in a else a
            if sig_type not in state["alerted_escape_types"]:
                # 检查是否需要二次确认（评分 >= 20 的信号）
                pre_key = f"{sig_type}_{data['time'][:5]}"
                if score >= 20 and sig_type not in state.get("pre_alert", {}):
                    # 首次预警，标记但不推送
                    if "pre_alert" not in state:
                        state["pre_alert"] = {}
                    state["pre_alert"][sig_type] = {
                        "first_seen": data["time"],
                        "score": score
                    }
                    save_state(state)
                    print(f"[{datetime.now().isoformat()}] [预警] {sig_type} 标记观察中（下次确认后推送）")
                else:
                    state["alerted_escape_types"].append(sig_type)
                    new_alerts.append(a)

        # ====== 发送微信提醒 ======
        if new_alerts:
            alert_text = "\n".join(new_alerts)
            with open(ALERT_LOG, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now().isoformat()}] {data['time']} {data['current']} fund={fund_data.get('main_net') if fund_data else 'N/A'} {new_alerts}\n")
            print(f"[{datetime.now().isoformat()}] ALERT {data['time']} {data['current']} {new_alerts}")

            is_critical = score >= 40
            change_pct = (data["current"]/data["previous_close"]-1)*100

            # 补充板块信息到微信消息
            msg = alert_text
            if sector_chg is not None:
                msg += f"\n关联板块涨跌: {sector_chg:+.1f}%"

            popup(
                title=f"航新科技 {data['current']} ({change_pct:+.1f}%)",
                message=msg,
                critical=is_critical
            )
        else:
            change_pct = (data["current"]/data["previous_close"]-1)*100
            fund_str = f"主力{fund_data['main_net']/1e4:.0f}万" if fund_data else "无资金"
            sector_str = f" 低空{sector_chg:+.1f}%" if sector_chg is not None else ""
            print(f"[{datetime.now().isoformat()}] {data['time']} {STOCK_NAME} {data['current']} ({change_pct:+.2f}%) 评分:{score} {fund_str}{sector_str}")

        save_state(state)
        time.sleep(FETCH_INTERVAL)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n监控已停止")
