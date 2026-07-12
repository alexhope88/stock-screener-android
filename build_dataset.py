# -*- coding: utf-8 -*-
"""
智能选股系统 —— 数据管道
================================
功能：
  1. 优先尝试通过 akshare 拉取真实 A 股行情 + 基本面数据（需网络）。
  2. 网络不可用时，生成一套“演示样本数据”（标注清晰），保证系统可完整体验。
  3. 统一特征字段与因子定义，输出 data/stocks.json 供前端选股引擎使用。

因子定义（FACTORS）是前端 UI 的唯一来源：阈值、方向、权重、分类全部在此定义，
前端据此动态渲染控件，保证“单一事实来源”。
"""
import json
import os
import random
import datetime
import time
import ssl
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data")
OUT_FILE = os.path.join(OUT_DIR, "stocks.json")
SEED = 20260710
N_SAMPLE = 520  # 样本股票数量

random.seed(SEED)


# ---------------------------------------------------------------------------
# 因子定义：前端据此动态渲染控件
# direction: "lower" 越低越好(如PE/PB)； "higher" 越高越好(如ROE/成长)
# filter:    "max" 上限过滤 | "min" 下限过滤 | "range" 区间 | "none" 仅评分
# weight:    默认权重（参与综合评分时的相对重要性）
# ---------------------------------------------------------------------------
FACTORS = [
    # ---- 价值 ----
    {"key": "pe_ttm", "label": "市盈率 PE(TTM)", "category": "价值", "unit": "倍",
     "direction": "lower", "filter": "max", "default_max": 30, "default_min": 0,
     "weight": 1.0, "decimals": 1, "enabled": True,
     "desc": "股价相对于每股收益的估值，越低越便宜（亏损股剔除）。"},
    {"key": "pb", "label": "市净率 PB", "category": "价值", "unit": "倍",
     "direction": "lower", "filter": "max", "default_max": 5, "default_min": 0,
     "weight": 0.8, "decimals": 2, "enabled": True,
     "desc": "股价相对于每股净资产，破净(<1)通常被视为低估值。"},
    {"key": "ps", "label": "市销率 PS", "category": "价值", "unit": "倍",
     "direction": "lower", "filter": "max", "default_max": 10, "default_min": 0,
     "weight": 0.4, "decimals": 2, "enabled": False,
     "desc": "股价相对于每股营收，适用于尚处投入期、利润未释放的公司。"},
    {"key": "dividend_yield", "label": "股息率", "category": "价值", "unit": "%",
     "direction": "higher", "filter": "min", "default_min": 1.5, "default_max": 100,
     "weight": 0.6, "decimals": 2, "enabled": False,
     "desc": "每股分红/股价，反映股东现金回报，红利策略核心指标。"},

    # ---- 质量 ----
    {"key": "roe", "label": "净资产收益率 ROE", "category": "质量", "unit": "%",
     "direction": "higher", "filter": "min", "default_min": 10, "default_max": 100,
     "weight": 1.0, "decimals": 1, "enabled": True,
     "desc": "净利润/净资产，衡量公司用股东资本赚钱的能力，长期投资的基石。"},
    {"key": "gross_margin", "label": "毛利率", "category": "质量", "unit": "%",
     "direction": "higher", "filter": "min", "default_min": 20, "default_max": 100,
     "weight": 0.5, "decimals": 1, "enabled": False,
     "desc": "（营收-成本）/营收，反映产品竞争力与定价权。"},
    {"key": "debt_ratio", "label": "资产负债率", "category": "质量", "unit": "%",
     "direction": "lower", "filter": "max", "default_max": 70, "default_min": 0,
     "weight": 0.5, "decimals": 1, "enabled": False,
     "desc": "总负债/总资产，过高意味着财务杠杆与偿债风险。"},

    # ---- 成长 ----
    {"key": "net_profit_yoy", "label": "净利润同比", "category": "成长", "unit": "%",
     "direction": "higher", "filter": "min", "default_min": 10, "default_max": 500,
     "weight": 1.0, "decimals": 1, "enabled": True,
     "desc": "当期净利润较去年同期增速，成长股核心指标。"},
    {"key": "revenue_yoy", "label": "营收同比", "category": "成长", "unit": "%",
     "direction": "higher", "filter": "min", "default_min": 10, "default_max": 500,
     "weight": 0.7, "decimals": 1, "enabled": False,
     "desc": "当期营业收入较去年同期增速，验证成长真实性。"},

    # ---- 动量/技术 ----
    {"key": "change_pct", "label": "当日涨跌幅", "category": "动量", "unit": "%",
     "direction": "higher", "filter": "none", "default_min": -10, "default_max": 10,
     "weight": 0.4, "decimals": 2, "enabled": True,
     "desc": "当日价格变动，反映短期市场情绪与动量。"},
    {"key": "turnover", "label": "换手率", "category": "动量", "unit": "%",
     "direction": "higher", "filter": "max", "default_max": 15, "default_min": 0,
     "weight": 0.3, "decimals": 2, "enabled": False,
     "desc": "成交活跃度，过高警惕炒作，过低缺乏关注。"},
    {"key": "volume_ratio", "label": "量比", "category": "动量", "unit": "倍",
     "direction": "higher", "filter": "min", "default_min": 1.0, "default_max": 10,
     "weight": 0.3, "decimals": 2, "enabled": False,
     "desc": "当日成交量/近5日均量，放量预示资金关注。"},

    # ---- 规模 ----
    {"key": "total_mv", "label": "总市值", "category": "规模", "unit": "亿",
     "direction": "higher", "filter": "range", "default_min": 50, "default_max": 5000,
     "weight": 0.3, "decimals": 0, "enabled": True,
     "desc": "公司体量，可据此区分大盘/中盘/小盘风格。"},
]


# ---------------------------------------------------------------------------
# 技术形态因子（需历史 K 线计算；仅样本模式计算并启用）
# ---------------------------------------------------------------------------
TECH_FACTORS = [
    {"key": "ma_bullish", "label": "均线多头排列", "category": "技术形态", "unit": "",
     "direction": "higher", "filter": "min", "default_min": 1, "default_max": 1,
     "weight": 1.5, "decimals": 0, "enabled": True,
     "desc": "MA5>MA10>MA20>MA60 且现价站上 MA5，经典多头趋势形态，趋势跟随核心信号。"},
    {"key": "vol_double_10d", "label": "近10日翻倍成交量", "category": "技术形态", "unit": "",
     "direction": "higher", "filter": "min", "default_min": 1, "default_max": 1,
     "weight": 1.2, "decimals": 0, "enabled": True,
     "desc": "最近10个交易日内出现当日成交量≥前一日2倍的“倍量柱”，资金放量介入信号。"},
]


def generate_history(stock, days=90):
    """为样本股票生成日线序列并计算技术形态信号（非真实行情）。"""
    price = stock["price"]
    # 趋势漂移：约38%上行(易形成多头排列)、35%下行、27%震荡
    r = random.random()
    if r < 0.38:
        drift = 0.0045
    elif r < 0.73:
        drift = -0.0035
    else:
        drift = 0.0
    sigma = 0.022
    start = price / ((1 + drift) ** days) * random.uniform(0.97, 1.03)
    closes = []
    p = start
    for _ in range(days):
        p = p * (1 + random.gauss(drift, sigma))
        closes.append(round(max(p, 0.5), 2))
    if closes[-1]:
        scale = price / closes[-1]
        closes = [round(c * scale, 2) for c in closes]

    base = random.uniform(0.6, 1.6)
    vols = [round(base * random.uniform(0.55, 1.5), 3) for _ in range(days)]
    if random.random() < 0.40:
        d = days - random.randint(1, 10)
        vols[d] = round(base * random.uniform(2.05, 3.4), 3)

    double_10d = 0
    for i in range(days - 10, days):
        if i > 0 and vols[i] >= 2 * vols[i - 1]:
            double_10d = 1
            break

    def ma(arr, n):
        return sum(arr[-n:]) / n if len(arr) >= n else None

    ma5, ma10, ma20, ma60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 60)
    bullish = 1 if (ma5 and ma10 and ma20 and ma60
                    and closes[-1] > ma5 > ma10 > ma20 > ma60) else 0

    return {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "ma_bullish": bullish,
        "vol_double_10d": double_10d,
        "spark": closes[-30:],
    }


# ---------------------------------------------------------------------------
# 行业画像：不同行业给出不同的指标分布，使样本更接近真实 A 股结构
# 每个画像给出各指标采样的参数（均值/范围）
# ---------------------------------------------------------------------------
SECTORS = {
    "银行": dict(weight=18, pe=(4, 9), pb=(0.4, 1.1), ps=(1, 4), div=(3.5, 6.5),
                 roe=(9, 16), gm=(20, 40), debt=(88, 94), np=(0, 12), rev=(-2, 12),
                 mv=(1500, 18000), to=(0.2, 1.5), vr=(0.7, 1.6)),
    "白酒/饮料": dict(weight=6, pe=(18, 55), pb=(4, 14), ps=(5, 18), div=(1.5, 4),
                     roe=(20, 35), gm=(60, 85), debt=(15, 40), np=(10, 35), rev=(8, 28),
                     mv=(800, 22000), to=(0.4, 2.5), vr=(0.8, 1.8)),
    "家电": dict(weight=6, pe=(10, 22), pb=(2, 5), ps=(0.8, 2.5), div=(2, 5),
                 roe=(15, 28), gm=(22, 38), debt=(45, 68), np=(5, 25), rev=(3, 18),
                 mv=(300, 6000), to=(0.6, 2.5), vr=(0.8, 1.8)),
    "医药": dict(weight=10, pe=(18, 50), pb=(2.5, 8), ps=(3, 12), div=(0.5, 2.5),
                 roe=(10, 25), gm=(45, 80), debt=(25, 55), np=(5, 40), rev=(5, 30),
                 mv=(150, 4000), to=(0.8, 3.5), vr=(0.8, 2.0)),
    "医疗器械": dict(weight=5, pe=(20, 60), pb=(3, 9), ps=(4, 14), div=(0.3, 2),
                     roe=(10, 24), gm=(50, 75), debt=(20, 50), np=(5, 45), rev=(5, 35),
                     mv=(120, 2500), to=(1, 4), vr=(0.9, 2.2)),
    "半导体": dict(weight=8, pe=(30, 90), pb=(3, 10), ps=(4, 16), div=(0, 1),
                   roe=(8, 22), gm=(35, 60), debt=(25, 55), np=(10, 80), rev=(8, 50),
                   mv=(150, 5000), to=(1.5, 6), vr=(1, 3)),
    "新能源/锂电": dict(weight=9, pe=(18, 65), pb=(2.5, 9), ps=(2, 10), div=(0.3, 2.5),
                       roe=(10, 28), gm=(15, 40), debt=(40, 70), np=(10, 90), rev=(10, 60),
                       mv=(300, 9000), to=(1, 5), vr=(1, 2.8)),
    "光伏": dict(weight=5, pe=(12, 45), pb=(1.5, 6), ps=(1.5, 7), div=(0.5, 2.5),
                 roe=(8, 24), gm=(12, 35), debt=(45, 72), np=(-10, 60), rev=(0, 45),
                 mv=(200, 4000), to=(1, 4.5), vr=(1, 2.8)),
    "汽车/零部件": dict(weight=7, pe=(10, 40), pb=(1.2, 5), ps=(0.6, 3), div=(1, 4),
                       roe=(8, 22), gm=(12, 30), debt=(45, 70), np=(0, 50), rev=(2, 35),
                       mv=(250, 7000), to=(0.8, 4), vr=(0.9, 2.5)),
    "食品/消费": dict(weight=6, pe=(18, 45), pb=(3, 9), ps=(1.5, 6), div=(1, 3.5),
                     roe=(12, 26), gm=(25, 50), debt=(25, 55), np=(5, 30), rev=(5, 22),
                     mv=(200, 5000), to=(0.6, 3), vr=(0.8, 2.0)),
    "化工/材料": dict(weight=8, pe=(8, 30), pb=(1, 4), ps=(0.8, 4), div=(1, 4),
                     roe=(6, 20), gm=(12, 35), debt=(40, 68), np=(-5, 50), rev=(0, 35),
                     mv=(150, 4000), to=(0.8, 4), vr=(0.9, 2.5)),
    "钢铁/有色": dict(weight=6, pe=(6, 25), pb=(0.7, 2.5), ps=(0.5, 2.5), div=(1.5, 5),
                     roe=(5, 18), gm=(8, 25), debt=(45, 70), np=(-15, 60), rev=(-5, 30),
                     mv=(150, 3000), to=(0.6, 3.5), vr=(0.9, 2.4)),
    "公用事业": dict(weight=5, pe=(10, 22), pb=(1, 3), ps=(1.2, 4), div=(2, 5),
                    roe=(8, 16), gm=(20, 40), debt=(50, 75), np=(0, 18), rev=(0, 15),
                    mv=(300, 5000), to=(0.3, 1.8), vr=(0.7, 1.6)),
    "地产": dict(weight=5, pe=(5, 20), pb=(0.5, 1.8), ps=(0.5, 2.5), div=(1, 4),
                 roe=(4, 16), gm=(15, 35), debt=(70, 90), np=(-20, 30), rev=(-15, 25),
                 mv=(150, 3500), to=(0.5, 3), vr=(0.8, 2.2)),
    "软件/计算机": dict(weight=7, pe=(25, 80), pb=(3, 10), ps=(3, 14), div=(0, 1.5),
                       roe=(8, 22), gm=(40, 70), debt=(20, 50), np=(5, 60), rev=(5, 45),
                       mv=(150, 4000), to=(1.2, 5), vr=(1, 2.8)),
    "军工": dict(weight=4, pe=(30, 75), pb=(2.5, 7), ps=(3, 11), div=(0.3, 1.5),
                 roe=(6, 18), gm=(30, 55), debt=(40, 65), np=(5, 40), rev=(3, 30),
                 mv=(200, 3500), to=(1, 4), vr=(0.9, 2.5)),
    "煤炭": dict(weight=4, pe=(5, 14), pb=(0.8, 2), ps=(0.8, 2.5), div=(3, 7),
                 roe=(10, 22), gm=(25, 45), debt=(40, 65), np=(-10, 40), rev=(-10, 25),
                 mv=(300, 5000), to=(0.5, 2.5), vr=(0.8, 2.0)),
    "通信/运营商": dict(weight=4, pe=(12, 25), pb=(1, 3), ps=(1, 3), div=(2.5, 5),
                       roe=(8, 16), gm=(28, 45), debt=(45, 68), np=(2, 18), rev=(3, 15),
                       mv=(1500, 22000), to=(0.4, 2), vr=(0.8, 1.8)),
    "农业/养殖": dict(weight=3, pe=(10, 40), pb=(1.5, 5), ps=(1, 5), div=(0.5, 3),
                     roe=(5, 22), gm=(12, 40), debt=(40, 70), np=(-30, 80), rev=(-10, 40),
                     mv=(150, 3000), to=(0.8, 4), vr=(0.9, 2.4)),
}


# 名称生成素材
NAME_PREFIX = ["华", "中", "远", "泰", "安", "瑞", "宏", "盛", "联", "创", "科", "智", "云",
               "星", "海", "天", "金", "晶", "鼎", "宇", "通", "顺", "康", "宁", "源", "能",
               "博", "新", "高", "德", "润", "恒", "佳", "卓", "晟", "耀", "锦", "拓", "迈",
               "普", "诺", "贝", "昱", "熵", "光", "风", "蓝", "广", "汇", "聚", "格", "英"]
NAME_MID = ["", "亿", "嘉", "信", "达", "辰", "兴", "邦", "威", "扬", "禾", "川", "元", "方",
            "锐", "驰", "凯", "微", "芯", "能", "泰", "盟", "翔", "润", "泽", "阳", "辉"]
SECTOR_NOUN = {
    "银行": "银行", "白酒/饮料": "酒业", "家电": "电器", "医药": "药业", "医疗器械": "医疗",
    "半导体": "半导体", "新能源/锂电": "新能", "光伏": "光伏", "汽车/零部件": "汽车",
    "食品/消费": "食品", "化工/材料": "材料", "钢铁/有色": "金属", "公用事业": "公用",
    "地产": "地产", "软件/计算机": "软件", "军工": "防务", "煤炭": "能源",
    "通信/运营商": "通信", "农业/养殖": "农牧",
}


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def sample_range(r, decimals=2):
    a, b = r
    if a == b:
        return round(a, decimals)
    val = random.uniform(a, b)
    return round(val, decimals)


def build_sample():
    """生成演示样本数据集（标注清晰：非真实行情）。"""
    total_w = sum(s["weight"] for s in SECTORS.values())
    plan = {}
    assigned = 0
    for name, s in SECTORS.items():
        n = round(N_SAMPLE * s["weight"] / total_w)
        plan[name] = n
        assigned += n
    diff = N_SAMPLE - assigned
    first = next(iter(plan))
    plan[first] += diff

    used_names = set()
    stocks = []
    seq = 0
    for sector, s in SECTORS.items():
        for _ in range(plan[sector]):
            seq += 1
            for _try in range(50):
                nm = (random.choice(NAME_PREFIX) + random.choice(NAME_MID)
                      + random.choice(NAME_MID) + SECTOR_NOUN[sector])
                if nm not in used_names:
                    used_names.add(nm)
                    break
            else:
                nm = SECTOR_NOUN[sector] + str(seq)
            r = random.random()
            if r < 0.45:
                code = "60" + str(random.randint(1000, 9999))
            elif r < 0.55:
                code = "68" + str(random.randint(1000, 9999))
            elif r < 0.85:
                code = "00" + str(random.randint(1000, 9999))
            else:
                code = "30" + str(random.randint(1000, 9999))

            pe = sample_range(s["pe"], 1)
            pb = sample_range(s["pb"], 2)
            ps = sample_range(s["ps"], 2)
            div = sample_range(s["div"], 2)
            roe = sample_range(s["roe"], 1)
            gm = sample_range(s["gm"], 1)
            debt = sample_range(s["debt"], 1)
            np_yoy = sample_range(s["np"], 1)
            rev_yoy = sample_range(s["rev"], 1)
            mv = sample_range(s["mv"], 0)
            circ_mv = round(mv * random.uniform(0.4, 0.95), 0)
            to = sample_range(s["to"], 2)
            vr = sample_range(s["vr"], 2)

            shares = max(1.0, mv / random.uniform(8, 60))
            price = round(mv * 1e8 / (shares * 1e8), 2)
            change = round(random.uniform(-9.8, 9.8), 2)
            eps = round(price / pe, 2) if pe > 0 else 0.0

            rec = {
                "code": code, "name": nm, "sector": sector,
                "price": price, "change_pct": change,
                "pe_ttm": pe, "pb": pb, "ps": ps, "dividend_yield": div,
                "total_mv": mv, "circ_mv": circ_mv,
                "turnover": to, "volume_ratio": vr,
                "roe": roe, "gross_margin": gm, "debt_ratio": debt,
                "eps": eps, "net_profit_yoy": np_yoy, "revenue_yoy": rev_yoy,
            }
            rec.update(generate_history(rec))
            stocks.append(rec)
    return stocks


def _http_get(url, ref=None, retries=4, timeout=15):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if ref:
        headers["Referer"] = ref
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise last


def _tof(v):
    try:
        if v in (None, "", "null"):
            return None
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _sina_universe():
    """新浪批量行情：全 A 股代码/名称/价格/涨跌幅/PE/PB/市值/换手率。"""
    stocks = []
    page = 1
    num = 100
    while page <= 200:
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData?page=%d&num=%d&node=hs_a&sort=symbol&asc=1" % (page, num))
        txt = _http_get(url, ref="https://finance.sina.com.cn/")
        arr = json.loads(txt) if txt else []
        if not arr:
            break
        for x in arr:
            pe = _tof(x.get("per"))
            pb = _tof(x.get("pb"))
            mk = _tof(x.get("mktcap"))
            nm = _tof(x.get("nmc"))
            stocks.append({
                "code": x.get("code"), "name": x.get("name"), "sector": "—",
                "price": _tof(x.get("trade")),
                "change_pct": _tof(x.get("changepercent")),
                "pe_ttm": pe if pe and pe > 0 else None,
                "pb": pb if pb and pb > 0 else None,
                "ps": None, "dividend_yield": None,
                "total_mv": round(mk / 1e8, 2) if mk else None,
                "circ_mv": round(nm / 1e8, 2) if nm else None,
                "turnover": _tof(x.get("turnoverratio")),
                "volume_ratio": None,
                "roe": None, "gross_margin": None, "debt_ratio": None,
                "eps": None, "net_profit_yoy": None, "revenue_yoy": None,
            })
        if len(arr) < num:
            break
        page += 1
        time.sleep(0.08)
    return stocks


def _em_yjbb(stocks):
    """东财业绩报表：ROE/EPS/营收同比/净利同比，按“最近一个披露充分的报告期”合并。"""
    by_code = {s["code"]: s for s in stocks}
    cols = "SECURITY_CODE,REPORTDATE,WEIGHTAVG_ROE,BASIC_EPS,YSTZ,SJLTZ"
    # 生成最近 8 个季末日期，逐个探测，取首个披露家数>1000 的报告期
    today = datetime.date.today()
    qe = []
    y, m = today.year, today.month
    for _ in range(8):
        if m >= 10:
            qe.append("%d-09-30" % y); m = 6
        elif m >= 7:
            qe.append("%d-06-30" % y); m = 3
        elif m >= 4:
            qe.append("%d-03-31" % y); m = 12; y -= 1
        else:
            qe.append("%d-12-31" % (y - 1)); m = 9
    report_date = None
    for rd in qe:
        try:
            u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
                 "sortColumns=SECURITY_CODE&sortTypes=1&pageSize=1&pageNumber=1&"
                 "reportName=RPT_LICO_FN_CPD&columns=%s&filter=(REPORTDATE=%%27%s%%27)" % (cols, rd))
            d = json.loads(_http_get(u))
            cnt = d.get("result", {}).get("count", 0) if d.get("result") else 0
            if cnt and cnt > 1000:
                report_date = rd
                break
        except Exception:
            continue
    if not report_date:
        return
    page = 1
    while page <= 40:
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
               "sortColumns=SECURITY_CODE&sortTypes=1&pageSize=500&pageNumber=%d&"
               "reportName=RPT_LICO_FN_CPD&columns=%s&filter=(REPORTDATE=%%27%s%%27)"
               % (page, cols, report_date))
        d = json.loads(_http_get(url))
        if not d.get("result") or not d["result"].get("data"):
            break
        for row in d["result"]["data"]:
            s = by_code.get(row.get("SECURITY_CODE"))
            if s:
                s["roe"] = _tof(row.get("WEIGHTAVG_ROE"))
                s["eps"] = _tof(row.get("BASIC_EPS"))
                s["revenue_yoy"] = _tof(row.get("YSTZ"))
                s["net_profit_yoy"] = _tof(row.get("SJLTZ"))
        if len(d["result"]["data"]) < 500:
            break
        page += 1
        time.sleep(0.1)


def _tencent_symbol(code):
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("4", "8", "9")):
        return "bj" + code
    return "sz" + code


def _tencent_kline(stocks, top_n=400):
    """腾讯前复权日K线：为市值前 top_n 只计算均线多头排列与近10日倍量。含失败重试。"""
    cands = [s for s in stocks if s.get("total_mv")]
    cands.sort(key=lambda s: s["total_mv"], reverse=True)
    cands = cands[:top_n]

    def fill(s):
        sym = _tencent_symbol(s["code"])
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,100,qfq" % sym)
        d = json.loads(_http_get(url, retries=5, timeout=14))
        kls = []
        try:
            kls = d["data"][sym].get("qfqday") or d["data"][sym].get("day") or []
        except (KeyError, TypeError):
            return False
        if len(kls) < 20:
            return False
        closes = [float(r[2]) for r in kls]
        vols = [float(r[5]) for r in kls if len(r) > 5]
        vols = vols[-len(closes):]

        def ma(arr, n):
            return sum(arr[-n:]) / n if len(arr) >= n else None

        ma5, ma10, ma20, ma60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 60)
        s["ma5"] = round(ma5, 2) if ma5 else None
        s["ma10"] = round(ma10, 2) if ma10 else None
        s["ma20"] = round(ma20, 2) if ma20 else None
        s["ma60"] = round(ma60, 2) if ma60 else None
        s["ma_bullish"] = 1 if (ma5 and ma10 and ma20 and ma60
                               and closes[-1] > ma5 > ma10 > ma20 > ma60) else 0
        double = 0
        for i in range(len(vols) - 10, len(vols)):
            if i > 0 and vols[i] >= 2 * vols[i - 1]:
                double = 1
                break
        s["vol_double_10d"] = double
        s["spark"] = closes[-30:]
        return True

    done = 0
    pending = list(cands)
    for attempt in (1, 2):  # 两轮：第二轮专攻第一轮失败的
        still = []
        for s in pending:
            try:
                if fill(s):
                    done += 1
                else:
                    still.append(s)
            except Exception:
                still.append(s)
            time.sleep(0.08 if attempt == 1 else 0.25)
        pending = still
        if not pending:
            break
    return done


def fetch_real():
    """真实 A 股多源抓取：新浪行情 + 东财业绩 + 腾讯K线(技术形态)。失败返回 None。"""
    try:
        print("  [1/3] 新浪批量行情 ...")
        stocks = _sina_universe()
        if not stocks:
            return None
        print("      股票数:", len(stocks))
        print("  [2/3] 东财业绩报表(ROE/成长) ...")
        _em_yjbb(stocks)
        print("  [3/3] 腾讯日K线(均线/倍量, 市值前400) ...")
        done = _tencent_kline(stocks, top_n=400)
        print("      技术形态覆盖:", done)
        # 清洗 NaN
        for st in stocks:
            for k, v in list(st.items()):
                if isinstance(v, float) and v != v:
                    st[k] = None
        return stocks
    except Exception as e:
        print("  fetch_real 失败:", repr(e)[:200])
        return None


def _existing_real_data():
    """若已存在真实数据文件，返回其路径；否则返回 None。"""
    if os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("meta", {}).get("source") == "real":
                return OUT_FILE
        except Exception:
            pass
    return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    stocks = fetch_real()
    if stocks is None:
        # 抓取失败：若磁盘上已有真实数据，保留它，避免把真实数据覆盖成样本。
        keep = _existing_real_data()
        if keep:
            print(f"[WARN] 真实数据抓取失败，保留上次成功生成的真实数据: {keep}")
            print(f"[WARN] 本次未覆盖写入。请检查网络后重跑本脚本。")
            return
        # 首次运行且抓不到真实数据：退回样本，保证系统可用。
        stocks = build_sample()
        source = "sample"
        note = ("演示样本数据：当前环境无法连接行情接口，已用按行业画像生成的合成数据填充，"
                "指标为示意值，仅用于体验选股逻辑，不构成任何投资建议。配置网络后运行本脚本可接入真实 A 股数据。")
        tech_ok = True
    else:
        source = "real"
        tech_ok = any(s.get("ma_bullish") is not None for s in stocks)
        note = ("真实 A 股数据（新浪行情 + 东财业绩 + 腾讯日K线）。"
                "技术形态(均线/倍量)覆盖市值前400只；股息率/毛利率/资产负债率/市销率/量比等字段"
                "公开接口未提供，已自动隐藏。数据仅供研究，不构成投资建议。")

    # 基础因子 + 技术因子(仅当有K线时)
    all_factors = FACTORS + (TECH_FACTORS if tech_ok else [])
    # 真实模式下：丢弃全样本都无数据的因子，避免误开导致结果为空
    if source == "real":
        keys_with_data = set()
        for f in all_factors:
            if any((s.get(f["key"]) is not None) for s in stocks):
                keys_with_data.add(f["key"])
        final_factors = [f for f in all_factors if f["key"] in keys_with_data]
        if not tech_ok:
            final_factors = [f for f in final_factors if f["category"] != "技术形态"]
    else:
        final_factors = all_factors

    payload = {
        "meta": {
            "source": source,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(stocks),
            "note": note,
            "factors": final_factors,
            "categories": list(dict.fromkeys(f["category"] for f in final_factors)),
        },
        "stocks": stocks,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    # 同时写出 data.js：供页面以 <script> 方式直接内嵌加载，规避 file:// 下 fetch 被拦截
    js_file = os.path.join(OUT_DIR, "data.js")
    with open(js_file, "w", encoding="utf-8") as f:
        f.write("window.STOCK_DATA = ")
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write(";")
    print(f"已写出 {len(stocks)} 条股票 -> {OUT_FILE} (source={source})")
    print(f"已写出内嵌数据 -> {js_file}")


if __name__ == "__main__":
    main()
