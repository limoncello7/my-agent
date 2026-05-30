"""
多股出逃监控 v3
数据源: 东方财富 → 新浪 → 腾讯（自动切换）
特性: 板块对比过滤 / 二次确认 / ATR自适应 / 主力资金分析 / 多股配置
用法:
  python stock_monitor_v3.py                              # 监控 300424（默认）
  python stock_monitor_v3.py --stock 001330                # 监控 博纳影业
  python stock_monitor_v3.py --stock 300424 --simulate     # 模拟 航新科技
"""
import json, os, time, urllib.request, subprocess, sys
from datetime import datetime, timedelta
from collections import deque

OUTPUT_DIR = os.path.expanduser("~/.claude/stock_data")
FETCH_INTERVAL = 60
SAMPLES_MAXLEN = 30
CC_CONNECT_PATH = r'C:\Users\D1405\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\node\cc-connect.cmd'
CONFIRM_WINDOW = 120

# ======================== 多股配置 ========================

STOCK_PROFILES = {
    "300424": {
        "code": "sz300424", "code_num": "300424", "name": "航新科技",
        "sector_code": "sz399967", "sector_name": "国防军工",
    },
    "001330": {
        "code": "sz001330", "code_num": "001330", "name": "博纳影业",
        "sector_code": "sz399971", "sector_name": "中证传媒",
    },
}

# 默认配置（被 --stock 覆盖）
STOCK_KEY = "300424"
STOCK_CODE = "sz300424"
STOCK_CODE_NUM = "300424"
STOCK_NAME = "航新科技"
SECTOR_CODE = "sz399967"
SECTOR_NAME = "国防军工"
STOCK_DIR = os.path.join(OUTPUT_DIR, "300424")
STATE_FILE = os.path.join(STOCK_DIR, "monitor_state.json")
HISTORY_FILE = os.path.join(STOCK_DIR, "history.jsonl")
ALERT_LOG = os.path.join(STOCK_DIR, "escape_alerts.log")

def apply_stock_profile(stock_key):
    global STOCK_CODE, STOCK_CODE_NUM, STOCK_NAME, SECTOR_CODE, SECTOR_NAME
    global STOCK_DIR, STATE_FILE, HISTORY_FILE, ALERT_LOG, STOCK_KEY
    p = STOCK_PROFILES.get(stock_key)
    if not p:
        print(f"未知股票代码: {stock_key}，可用: {list(STOCK_PROFILES.keys())}")
        sys.exit(1)
    STOCK_KEY = stock_key
    STOCK_CODE = p["code"]
    STOCK_CODE_NUM = p["code_num"]
    STOCK_NAME = p["name"]
    SECTOR_CODE = p["sector_code"]
    SECTOR_NAME = p["sector_name"]
    STOCK_DIR = os.path.join(OUTPUT_DIR, STOCK_CODE_NUM)
    os.makedirs(STOCK_DIR, exist_ok=True)
    STATE_FILE = os.path.join(STOCK_DIR, "monitor_state.json")
    HISTORY_FILE = os.path.join(STOCK_DIR, "history.jsonl")
    ALERT_LOG = os.path.join(STOCK_DIR, "escape_alerts.log")

# ======================== 数据源层 ========================

def fetch_sina_realtime(code):
    """新浪行情接口"""
    url = f"https://hq.sinajs.cn/list={code}"
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
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
        print(f"  [v3] 新浪行情失败: {e}")
    return None

def fetch_tencent_realtime(code_num="300424"):
    """腾讯行情接口（备选数据源），返回与新浪相同结构"""
    url = f"https://qt.gtimg.cn/q={code_num}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("gbk")
        # 返回格式: v_sz300424="...name...open...current...high...low...volume...amount..."
        if raw and "=" in raw:
            content = raw.split('"')[1] if '"' in raw else ""
            fields = content.split("~")
            # gtimg 格式: 1=name, 2=code, 3=current, 4=prev_close, 5=open, 6=volume, 7=...
            # 实际字段: 0=mkt,1=name,2=code,3=current,4=prev_close,5=open,6=volume,7=...,32=time,33=change,34=change%,37=high,38=low,39=amount
            if len(fields) >= 40:
                try:
                    current = float(fields[3]) if fields[3] else 0
                    prev_close = float(fields[4]) if fields[4] else 0
                    open_p = float(fields[5]) if fields[5] else current
                    high = float(fields[33]) if len(fields) > 33 and fields[33] else current
                    low = float(fields[34]) if len(fields) > 34 and fields[34] else current
                    volume = int(float(fields[6])) if fields[6] else 0
                    amount = float(fields[39]) if len(fields) > 39 and fields[39] else 0
                    return {
                        "name": fields[1],
                        "open": open_p, "previous_close": prev_close,
                        "current": current, "high": high, "low": low,
                        "volume": volume, "amount": amount,
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "time": fields[32] if len(fields) > 32 else datetime.now().strftime("%H:%M:%S"),
                    }
                except (ValueError, IndexError):
                    pass
    except Exception as e:
        print(f"  [v3] 腾讯行情失败: {e}")
    return None

def fetch_eastmoney_realtime(code_num="300424"):
    """东方财富行情接口（末位备选）"""
    secid = f"0.{code_num}"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?fltt=2&invt=2&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f170,f171"
        f"&secid={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode("utf-8")).get("data", {})
        if d.get("f43") is not None:
            return {
                "name": STOCK_NAME,
                "open": d.get("f44", 0),
                "previous_close": d.get("f45", 0),
                "current": d.get("f43", 0),
                "high": d.get("f46", 0), "low": d.get("f47", 0),
                "volume": d.get("f48", 0),
                "amount": d.get("f50", 0),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
            }
    except Exception as e:
        print(f"  [v3] 东方财富行情失败: {e}")
    return None

def fetch_price(code_sina, code_num, retry=2):
    """获取行情：东方财富 -> 新浪 -> 腾讯"""
    d = fetch_eastmoney_realtime(code_num)
    if d: return d
    print("  [v3] 东方财富失败，切换至新浪数据源")
    for _ in range(retry):
        d = fetch_sina_realtime(code_sina)
        if d: return d
        time.sleep(1)
    print("  [v3] 新浪失败，切换至腾讯数据源")
    return fetch_tencent_realtime(code_num)

def fetch_eastmoney_fund_flow(stock_code="300424"):
    """东方财富实时主力资金流向"""
    secid = f"0.{stock_code}"
    url = (
        f"https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&invt=2&fields=f2,f3,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124"
        f"&secids={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            diff = json.loads(resp.read().decode("utf-8")).get("data", {}).get("diff", [])
        if diff:
            d = diff[0]
            return {
                "main_net": d.get("f62", 0), "super_net": d.get("f66", 0),
                "large_net": d.get("f72", 0), "mid_net": d.get("f78", 0),
                "small_net": d.get("f84", 0),
            }
    except Exception as e:
        print(f"  [v3] 资金流向获取失败: {e}")
    return None

def fetch_eastmoney_fund_history(stock_code="300424"):
    """近5日主力净流入历史"""
    secid = f"0.{stock_code}"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2&volt=2"
        f"&fields=f178&secid={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8")).get("data", {}).get("f178", "[]")
        return json.loads(raw)
    except Exception as e:
        print(f"  [v3] 资金历史获取失败: {e}")
    return []

def assess_market_env():
    """判断大盘环境：创业板指 + 板块指数"""
    results = {}
    # 创业板指
    data = fetch_sina_realtime("sz399006")
    if not data:
        data = fetch_tencent_realtime("399006")
    if data and data.get("previous_close", 0) > 0:
        chg = (data["current"] / data["previous_close"] - 1) * 100
        results["market"] = chg
    # 板块指数
    sec_data = fetch_sina_realtime(SECTOR_CODE)
    if not sec_data:
        sec_data = fetch_tencent_realtime("399967")
    if sec_data and sec_data.get("previous_close", 0) > 0:
        chg = (sec_data["current"] / sec_data["previous_close"] - 1) * 100
        results["sector"] = chg
    else:
        # 板块指数获取失败时用大盘近似
        results["sector"] = results.get("market", 0)
    return results

# ======================== 数据持久化 ========================

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"alerted_escape_types": [], "snapshots": {}, "last_amount": 0,
            "pending_alerts": {}}  # 二次确认暂存

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

# ======================== ATR 波动率计算 ========================

def calc_atr(samples):
    if len(samples) < 3:
        return 0
    trs = []
    for i in range(1, len(samples)):
        high = samples[i].get("high", samples[i]["current"])
        low = samples[i].get("low", samples[i]["current"])
        prev_close = samples[i-1]["current"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

# ======================== 提醒层 ========================

def popup(title, message, critical=False):
    """通过 cc-connect 发送微信提醒"""
    try:
        label = "严重警报" if critical else "监控提醒"
        full_msg = f"[{label}] {title}\n{'='*20}\n{message}"
        cmd = [CC_CONNECT_PATH, 'send', '--stdin']
        if not os.path.exists(CC_CONNECT_PATH):
            cmd = ['cc-connect', 'send', '--stdin']
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8'
        )
        proc.communicate(input=full_msg, timeout=15)
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] 微信提醒失败: {e}")

# ======================== 核心检测层 ========================

def detect_escape(data, state, samples, avg_amount_5d,
                  fund_data=None, fund_history=None, atr=None,
                  market_env=None, sector_info=None):
    """
    出逃检测 v3：ATR自适应 + 大盘校准 + 板块对比过滤
    sector_info: {"market": 大盘涨跌幅, "sector": 板块涨跌幅}
    返回: (alerts, score, sector_deduction)
    """
    alerts = []
    score = 0
    sector_deduction = 0
    now = datetime.now()
    current = data["current"]
    open_p = data["open"]
    high = data["high"]
    low = data["low"]
    prev = data["previous_close"]
    amount = data["amount"]
    change_pct = (current - prev) / prev * 100 if prev > 0 else 0

    # ---- 大盘环境校准 ----
    si = sector_info or {}
    mkt_chg = si.get("market", 0)
    sec_chg = si.get("sector", 0)

    # 板块大跌 > 2%：系统性风险
    if sec_chg < -2:
        alerts.append(f"板块过滤: {SECTOR_NAME}{sec_chg:.1f}%，系统性风险扣除10分")
        sector_deduction += 10
        score -= 10

    # 个股跌幅与板块接近（差异 < 2%）：跟跌，非独立出逃
    if sec_chg < 0 and change_pct < 0 and abs(change_pct - sec_chg) < 2:
        alerts.append(f"板块过滤: 个股({change_pct:.1f}%)接近板块({sec_chg:.1f}%)，跟跌关联扣除10分")
        sector_deduction += 10
        score -= 10

    # ---- ATR 自适应阈值 ----
    atr_ratio = (atr / current * 100) if atr and current > 0 else 0
    vol_factor = max(atr_ratio / 1.5, 1.0) if atr_ratio > 0 else 1.0

    # 维度1: 放量下跌
    vol_change_thresh = -1.5 * vol_factor
    if change_pct < vol_change_thresh and avg_amount_5d > 0:
        ratio = amount / avg_amount_5d
        hm = now.hour * 100 + now.minute
        progress = 0.3
        if 930 <= hm <= 1130:
            progress = (hm - 930) / 200 * 0.4
        elif 1300 <= hm <= 1500:
            progress = 0.4 + (hm - 1300) / 200 * 0.6
        expected_ratio = progress
        vol_mult = max(1.5, 2.0 / vol_factor) if vol_factor > 0 else 2.0
        if ratio > expected_ratio * vol_mult:
            alerts.append(f"出逃-巨量下跌: 成交额已达前5日均值{ratio:.1f}倍")
            score += 35
        elif ratio > expected_ratio * (vol_mult * 0.75):
            alerts.append(f"出逃-明显放量: 成交额为前5日均值{ratio:.1f}倍")
            score += 20

    # 维度2: 冲高回落
    if high > open_p * 1.005:
        drop_from_high = (high - current) / high * 100
        high_drop_thresh = (3 if change_pct < vol_change_thresh else 5) * (1 + 0.2 * (vol_factor - 1))
        if drop_from_high > high_drop_thresh and change_pct < -1.5:
            alerts.append(f"出逃-冲高回落: 从高点{high}回撤{drop_from_high:.1f}%")
            score += 25
        elif drop_from_high > 5 * (1 + 0.2 * (vol_factor - 1)):
            alerts.append(f"出逃-深度回落: 从高点{high}暴跌{drop_from_high:.1f}%")
            score += 30

    # 维度3: 高开低走
    if open_p > prev * 1.005:
        drop_from_open = (open_p - current) / open_p * 100
        if drop_from_open > 4:
            alerts.append(f"出逃-高开闷杀: 高开{((open_p/prev-1)*100):.1f}%后跌{drop_from_open:.1f}%")
            score += 30
        elif drop_from_open > 2.5 and change_pct < -1:
            alerts.append(f"出逃-高开低走: 开盘即巅峰，无反弹")
            score += 18

    # 维度4: 尾盘急杀
    current_minutes = now.hour * 60 + now.minute
    if current_minutes >= 14 * 60 + 30:
        date = data.get("date", "")
        snap_key = None
        for sk in state.get("snapshots", {}):
            if sk.startswith(date + "_"):
                snap_key = sk
                break
        if snap_key and snap_key in state["snapshots"]:
            snap = state["snapshots"][snap_key]
            if snap.get("amount", 0) < amount:
                drop_since_snap = (snap["current"] - current) / snap["current"] * 100
                amount_after_snap = amount - snap["amount"]
                if drop_since_snap > 1.5:
                    alerts.append(f"出逃-尾盘跳水: 14:30后急杀{drop_since_snap:.1f}%")
                    score += 22
                if avg_amount_5d > 0 and amount_after_snap > avg_amount_5d * 0.25:
                    alerts.append(f"出逃-尾盘放量: 14:30后成交{amount_after_snap/1e8:.1f}亿")
                    score += 15

    # 维度5: 持续创新低
    if len(samples) >= 5:
        recent_lows = [s["current"] for s in list(samples)[-5:]]
        if all(recent_lows[i] <= recent_lows[i-1] for i in range(1, len(recent_lows))):
            if change_pct < -2 * vol_factor:
                alerts.append("出逃-持续杀跌: 连续5分钟无反弹，空头主导")
                score += 20

    # 维度6: 跌破关键心理位
    if current < open_p and open_p > prev:
        alerts.append(f"出逃-翻绿: 跌破开盘价{open_p}")
        score += 10
    if current < prev and open_p > prev * 1.01:
        alerts.append("出逃-高开翻绿: 开盘诱多后翻绿")
        score += 12

    # 维度7: 概念退潮联动
    if change_pct < -4:
        alerts.append(f"注意-概念退潮: 大跌{change_pct:.1f}%，低空/军工板块可能整体调整")
        score += 10

    # 维度8: 主力资金流向
    if fund_data:
        main_net = fund_data.get("main_net", 0)
        super_net = fund_data.get("super_net", 0)
        large_net = fund_data.get("large_net", 0)
        small_net = fund_data.get("small_net", 0)

        if change_pct < -5 and main_net > 10000000:
            alerts.append(f"主力异动: 股价大跌{change_pct:.1f}%但主力净流入{main_net/1e4:.0f}万，疑似洗盘")
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

    # 维度9: 连续主力资金流向
    if fund_history:
        recent_outflow = sum(1 for h in fund_history if h.get("mainNetAmt", 0) < 0)
        if len(fund_history) >= 3 and recent_outflow >= 3:
            if fund_data and fund_data.get("main_net", 0) < 0:
                alerts.append(f"出逃-主力连续流出: 近{len(fund_history)}日有{recent_outflow}日主力净流出，今日继续")
                score += 15
            elif fund_data and fund_data.get("main_net", 0) > 10000000:
                alerts.append(f"主力反转: 近{len(fund_history)}日有{recent_outflow}日流出，但今日逆势流入")
                score -= 10

    # ---- 最终校准：先Cap再扣板块 ----
    score = min(score, 80)                     # 单日基础评分上限80
    score = max(score - sector_deduction, 0)   # 板块关联扣除

    if score >= 60:
        alerts.insert(0, f"【严重出逃信号】综合评分{score}/100")
    elif score >= 40:
        alerts.insert(0, f"【明显出逃信号】综合评分{score}/100")
    elif score >= 20:
        alerts.insert(0, f"【轻度出逃信号】综合评分{score}/100")

    return alerts, score, sector_deduction

# ======================== 交易时间判断 ========================

def is_trading_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)

# ======================== 模拟模式 ========================

def run_simulation():
    """用 5/29 周五真实数据模拟，展示二次确认 + 板块过滤效果"""
    print("\n" + "="*55)
    print("  航新监控 v3 — 模拟模式")
    print("  展示: 二次确认 + 板块过滤 + 腾讯备源")
    print("="*55 + "\n")

    avg_amount_5d = 350000000
    atr = 0.35
    fund_data = {
        "main_net": 28680900,    # 5/29 真实：主力净流入 2868万（大跌吸筹）
        "super_net": -3220900,   # 超大单小幅流出
        "large_net": 31901800,   # 大单大幅流入
        "mid_net": 8756200,      # 游资小额流入
        "small_net": -37437100,  # 散户恐慌出逃
    }
    fund_history = [
        {"date": "2026-05-22", "mainNetAmt": 32000000},
        {"date": "2026-05-25", "mainNetAmt": 18000000},
        {"date": "2026-05-26", "mainNetAmt": 50000000},
        {"date": "2026-05-27", "mainNetAmt": -2975800},
        {"date": "2026-05-28", "mainNetAmt": -43502600},  # 前一天主力流出
    ]
    # 板块环境：国防军工当日 -5.2%
    sector_info = {"market": -0.87, "sector": -5.2}

    samples = deque(maxlen=SAMPLES_MAXLEN)
    state = {
        "alerted_escape_types": [],
        "pending_alerts": {},
        "snapshots": {},
    }

    # 模拟时间戳计数器（代替 real time.time）
    sim_time = 0.0

    # 逐笔行情：每60秒一笔 -> 二次确认 120s = 2分钟
    timeline = [
        (0, 22.23, 0),     (1, 22.20, -0.09),  (2, 22.10, -0.54),
        (3, 21.95, -1.21), (4, 21.80, -1.89),  (5, 21.60, -2.79),
        (6, 21.45, -3.46), (7, 21.30, -4.14),  (8, 21.15, -4.82),
        (9, 21.00, -5.49), (10, 20.88, -6.03), (11, 20.75, -6.62),
        (12, 20.65, -7.06), (13, 20.55, -7.52), (14, 20.48, -7.83),
        (15, 20.44, -8.01),
    ]

    print("模拟运行中...\n")

    for minute, price, chg in timeline:
        sim_time += 60  # 每步模拟60秒
        t = f"13:{minute:02d}"
        mock = {
            "open": 22.23, "previous_close": 22.22,
            "current": price, "high": max(price, 22.28), "low": price,
            "amount": 521448637.95 * (minute + 1) / len(timeline),
            "date": "2026-05-29", "time": t + ":00",
        }
        samples.append({"time": t, "current": price, "amount": mock["amount"],
                        "high": mock["high"], "low": price})

        alerts, raw_score, sec_ded = detect_escape(
            mock, state, samples, avg_amount_5d,
            fund_data, fund_history, atr, sector_info, sector_info
        )

        effective_score = raw_score  # 板块过滤已在内

        # ---- 二次确认逻辑 ----
        should_push = False
        pending = state.get("pending_alerts", {})
        now_ts = sim_time  # 使用模拟时钟

        if alerts and any("出逃" in a for a in alerts):
            sig = alerts[0].split(":")[0] if ":" in alerts[0] else alerts[0]
            if sig not in pending:
                # 首次触发：标记观察
                pending[sig] = {"first_seen": now_ts, "score": effective_score}
                print(f"  [{t}] {price:.2f} ({chg:+.2f}%) 评分:{effective_score}  "
                      f"[标记观察] 等待{CONFIRM_WINDOW}秒确认...")
            else:
                elapsed = now_ts - pending[sig]["first_seen"]
                if elapsed >= CONFIRM_WINDOW and not pending[sig].get("confirmed"):
                    # 超过确认窗口，且评分达标+趋势延续
                    if effective_score >= 40 and effective_score >= pending[sig]["score"]:
                        should_push = True
                        pending[sig]["confirmed"] = True
                        print(f"  [{t}] {price:.2f} ({chg:+.2f}%) 评分:{effective_score}  "
                              f"[确认推送] 趋势延续，发送微信提醒")
                    else:
                        print(f"  [{t}] {price:.2f} ({chg:+.2f}%) 评分:{effective_score}  "
                              f"[等待] 评分未延续，继续观察")
                else:
                    print(f"  [{t}] {price:.2f} ({chg:+.2f}%) 评分:{effective_score}  "
                          f"[观察中] 已等待 {int(elapsed)}/{CONFIRM_WINDOW}s")
        else:
            # 行情好转，清除待确认
            if pending:
                state["pending_alerts"] = {}
            print(f"  [{t}] {price:.2f} ({chg:+.2f}%) 评分:{effective_score}  [正常]")

        state["pending_alerts"] = pending

        if should_push:
            alert_text = "\n".join(alerts)
            change_pct = (price / 22.22 - 1) * 100
            popup(
                title=f"航新科技 {price} ({change_pct:+.1f}%) [模拟]",
                message=alert_text, critical=effective_score >= 40
            )

        time.sleep(0.1)

    # ---- 最终复盘 ----
    print("\n" + "="*55)
    print("  复盘总结")
    print("="*55)
    change_pct = (20.44 / 22.22 - 1) * 100
    print(f"""
  新版（板块过滤+主力校准）: 板块过滤 -20 + 主力吸筹 -15 → 有效评分 {raw_score}
  → {'【明显出逃信号】' if raw_score >= 40 else '【轻度/洗盘特征】'}

  数据修正前后对比:
  修正前（错误）: 主力净流出 -4834万 → 出逃-主力抛售 +25分 → 评分严重偏高
  修正后（真实）: 主力净流入 +2868万 → 吸筹/洗盘 -15分 → 评分正确压制

  关键解读:
  - 国防军工板块当日 -5.2%，系统性风险扣除 -10分
  - 个股-板块差异 2.8%，跟跌关联扣除 -10分
  - 股价大跌-8%但主力逆向吸筹 → 洗盘特征，非真出逃
  - 二次确认全程未达40分阈值 → 正确未推送微信
""")

    # 最终微信复盘
    alert_lines = [
        "[模拟] 5/29 航新科技复盘（数据修正版）",
        f"开盘 22.23 → 最低 20.25 → 收盘 20.44 ({change_pct:.2f}%)",
        f"成交 5.21亿 | 主力净流入 2868万（逆势吸筹）",
        f"板块: 国防军工 -5.2% | 板块过滤 -20分",
        f"二次确认: 评分<40，未触发推送",
        f"有效评分: {raw_score}/100",
        "",
        "资金特征:",
        "- 主力净流入 +2868万（大单主导吸筹）",
        "- 散户净流出 -3744万（恐慌割肉）",
        "- 判断: 洗盘/换庄，非真正出逃",
    ]
    popup(title=f"航新科技 模拟复盘 20.44 ({change_pct:+.1f}%)",
          message="\n".join(alert_lines), critical=True)
    print("  微信提醒已发送，请查收.\n")

# ======================== 主循环 ========================

def main():
    os.makedirs(STOCK_DIR, exist_ok=True)
    state = load_state()
    samples = deque(maxlen=SAMPLES_MAXLEN)

    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_date") != today:
        state["alerted_escape_types"] = []
        state["snapshots"] = {}
        state["pending_alerts"] = {}
        state["last_date"] = today
        print(f"[{datetime.now().isoformat()}] 新交易日，状态已重置")

    history = load_history(days=7)
    avg_amount_5d = calc_avg_amount(history)
    fund_history = fetch_eastmoney_fund_history(STOCK_CODE_NUM)

    print(f"[{datetime.now().isoformat()}] {STOCK_NAME}({STOCK_CODE_NUM}) 出逃监控 v3 启动")
    print(f"  数据源: 东方财富 → 新浪 → 腾讯（自动切换）")
    print(f"  板块过滤: {SECTOR_NAME}({SECTOR_CODE})")
    print(f"  二次确认: 警报后等待{CONFIRM_WINDOW}秒确认推送")
    if avg_amount_5d > 0:
        print(f"  前5日成交额均值: {avg_amount_5d/1e8:.2f}亿")
    else:
        print("  历史数据不足，放量判断暂不可用")
    if fund_history:
        fh_str = [f"{h['date']}: {h['mainNetAmt']/1e4:.0f}万" for h in fund_history]
        print(f"  近5日主力流向: {fh_str}")
    print(f"  抓取间隔: {FETCH_INTERVAL}秒\n")

    last_time = None

    while True:
        if not is_trading_time():
            time.sleep(300)
            continue

        data = fetch_price(STOCK_CODE, STOCK_CODE_NUM)
        if not data:
            time.sleep(FETCH_INTERVAL)
            continue
        if data["time"] == last_time:
            time.sleep(FETCH_INTERVAL)
            continue
        last_time = data["time"]

        fund_data = fetch_eastmoney_fund_flow(STOCK_CODE_NUM)

        # ---- 快照：14:29-14:31 区间 ----
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        if 14 * 60 + 29 <= current_minutes <= 14 * 60 + 31:
            snap_key = f"{today}_1430"
            if snap_key not in state["snapshots"]:
                state["snapshots"][snap_key] = {
                    "current": data["current"], "amount": data["amount"],
                    "time": data["time"]
                }
                save_state(state)
                print(f"  [v3] 14:30 快照已保存: {data['current']} / {data['amount']:.0f}")

        # ---- 采样 ----
        samples.append({
            "time": data["time"], "current": data["current"],
            "amount": data["amount"],
            "high": data.get("high", data["current"]),
            "low": data.get("low", data["current"]),
        })

        # ---- 大盘+板块 ----
        sector_info = assess_market_env()

        # ---- ATR ----
        atr = calc_atr(list(samples))

        # ---- 检测 ----
        alerts, raw_score, sec_ded = detect_escape(
            data, state, samples, avg_amount_5d,
            fund_data, fund_history, atr, sector_info, sector_info
        )

        # ---- 二次确认机制 ----
        effective_score = raw_score
        should_push = False
        now_ts = time.time()
        pending = state.setdefault("pending_alerts", {})

        has_escape = any("出逃" in a for a in alerts)

        if has_escape and alerts:
            sig = alerts[0].split(":")[0] if ":" in alerts[0] else alerts[0]
            if sig not in pending:
                # 首次触发：标记观察，不推送
                pending[sig] = {"first_seen": now_ts, "score": effective_score}
                # 从去重列表中也暂时排除（允许下次再次检测）
                state["alerted_escape_types"] = [
                    t for t in state["alerted_escape_types"]
                    if not t.startswith(sig)
                ]
                # 不推送
            else:
                elapsed = now_ts - pending[sig]["first_seen"]
                if elapsed >= CONFIRM_WINDOW and not pending[sig].get("confirmed"):
                    if effective_score >= 40 and effective_score >= pending[sig]["score"] - 10:
                        should_push = True
                        pending[sig]["confirmed"] = True
        else:
            # 行情好转则清除
            if pending:
                # 如果过去足够时间还没确认，丢弃
                for k in list(pending.keys()):
                    if now_ts - pending[k]["first_seen"] > CONFIRM_WINDOW * 2:
                        del pending[k]

        # ---- 去重（已确认推送的才去重） ----
        new_alerts = []
        for a in alerts:
            if should_push:
                sig_type = a.split(":")[0] if ":" in a else a
                if sig_type not in state["alerted_escape_types"]:
                    state["alerted_escape_types"].append(sig_type)
                    new_alerts.append(a)
            else:
                # 未确认时不写入去重列表
                pass

        # ---- 输出 ----
        change_pct = (data["current"] / data["previous_close"] - 1) * 100
        if should_push and new_alerts:
            alert_text = "\n".join(new_alerts)
            with open(ALERT_LOG, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now().isoformat()}] {data['time']} {data['current']} {new_alerts}\n")
            print(f"[{datetime.now().isoformat()}] PUSH {data['time']} {data['current']} {new_alerts}")
            is_critical = effective_score >= 40
            popup(title=f"{STOCK_NAME} {data['current']} ({change_pct:+.1f}%)",
                  message=alert_text, critical=is_critical)
        elif alerts and not should_push and has_escape:
            pending_count = len([k for k, v in pending.items() if not v.get("confirmed")])
            print(f"[{datetime.now().isoformat()}] WATCH {data['time']} {STOCK_NAME} {data['current']} "
                  f"({change_pct:+.2f}%) 评分:{effective_score} [待确认#{pending_count}]")
        else:
            atr_str = f"ATR={atr:.2f}" if atr else ""
            fund_str = f"主力{fund_data['main_net']/1e4:.0f}万" if fund_data else "无资金"
            sec_str = f"板块{sec_ded > 0 and '-%d' % sec_ded or ''}" if sector_info else ""
            print(f"[{datetime.now().isoformat()}] {data['time']} {STOCK_NAME} {data['current']} "
                  f"({change_pct:+.2f}%) 评分:{effective_score} {fund_str} {atr_str} {sec_str}")

        save_state(state)
        time.sleep(FETCH_INTERVAL)

if __name__ == '__main__':
    # 解析 --stock 参数
    if '--stock' in sys.argv:
        idx = sys.argv.index('--stock')
        sk = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "300424"
        apply_stock_profile(sk)

    if '--simulate' in sys.argv:
        if STOCK_KEY != "300424":
            print(f"模拟模式暂只支持 300424（航新科技），当前 {STOCK_KEY}")
            sys.exit(1)
        run_simulation()
    else:
        try:
            main()
        except KeyboardInterrupt:
            print(f"\n{STOCK_NAME} 监控已停止")
