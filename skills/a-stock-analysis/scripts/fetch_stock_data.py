#!/usr/bin/env python3
"""
A股个股深度数据获取脚本 (v3.0)
新增: 多数据源fallback（东方财富→腾讯→新浪）、盘后可用、各API独立失败不崩溃
K线: 日线 (新浪 scale=240)
用法: python fetch_stock_data.py <code> [--json]
"""

import urllib.request, json, re, sys, time

# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════
def _get(url, timeout=10, referer='https://quote.eastmoney.com/', decode=None, retries=3, extra_headers=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': referer,
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    if extra_headers: headers.update(extra_headers)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read()
            if decode == 'gb2312': return raw.decode('gb2312', errors='ignore')
            return raw.decode('utf-8', errors='ignore')
        except Exception as e:
            last_err = e
            if attempt < retries - 1: time.sleep(2.0 * (attempt + 1))
    raise last_err

def _secid(code):
    return f"{'1' if code.startswith(('6','5')) else '0'}.{code}"

# ═══════════════════════════════════════════════════════════
# 1. 实时行情（多数据源fallback）
#    优先级: 东方财富 → 腾讯 → 新浪
#    东方财富盘后关闭连接，腾讯和新浪盘后可用
# ═══════════════════════════════════════════════════════════
def fetch_quote_eastmoney(code):
    """东方财富行情API（开盘时段最优，盘后关闭）"""
    fields = 'f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,f162,f167,f169,f170,f168'
    url = f'https://push2.eastmoney.com/api/qt/stock/get?secid={_secid(code)}&fields={fields}'
    try:
        d = json.loads(_get(url, referer='https://quote.eastmoney.com/')).get('data')
        if not d: return None
        return {
            'name': d.get('f58',''), 'code': d.get('f57',code),
            'price':      round(d.get('f43',0)/100,2),
            'high':       round(d.get('f44',0)/100,2),
            'low':        round(d.get('f45',0)/100,2),
            'open':       round(d.get('f46',0)/100,2),
            'prev_close': round(d.get('f60',0)/100,2),
            'volume':     d.get('f47',0),
            'amount':     d.get('f48',0),
            'amplitude':  round(d.get('f50',0)/100,2),
            'change_pct': round(d.get('f169',0)/100,2),
            'change':     round(d.get('f170',0)/100,2),
            'turnover':   round(d.get('f168',0)/100,2) if d.get('f168') else None,
            'market_cap': round(d.get('f116',0)/1e8,1),
            'pe_ttm':     round(d.get('f162',0)/100,2) if d.get('f162') and d['f162']>0 else None,
            'pe_dynamic': round(d.get('f167',0)/100,2) if d.get('f167') and d['f167']>0 else None,
            'source': '东方财富',
        }
    except Exception:
        return None

def fetch_quote_tencent(code):
    """腾讯行情API（盘后可用，数据维度丰富）"""
    prefix = 'sz' if code.startswith(('0','3')) else 'sh'
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    try:
        raw = _get(url, referer='https://gu.qq.com/', decode='gb2312')
        m = re.search(r'v_\w+="([^"]+)"', raw)
        if not m: return None
        parts = m.group(1).split('~')
        # 腾讯API字段索引: 0=未知, 1=名称, 2=代码, 3=最新价, 4=昨收, 5=今开,
        # 6=成交量(手), 7=外盘, 8=内盘, 9=买一, 10=买一量, ... 19=卖一, 20=卖一量,
        # 30=时间戳, 31=涨跌额, 32=涨跌幅, 33=最高, 34=最低,
        # 37=成交额(万), 38=换手率, 39=PE, 43=涨幅, 44=最高, 45=最低, 46=量比, 47=跌停价, 48=涨停价
        price = float(parts[3]) if parts[3] else 0
        pe = float(parts[39]) if parts[39] and parts[39] != '0' else None
        market_cap = float(parts[45]) if parts[45] and parts[45] != '0' else None
        high = float(parts[33]) if parts[33] else 0
        low = float(parts[34]) if parts[34] else 0
        prev_close = float(parts[4]) if parts[4] else 0
        # 计算振幅: (最高-最低)/昨收
        amplitude = round((high - low) / prev_close * 100, 2) if prev_close and high and low else None
        return {
            'name': parts[1], 'code': parts[2],
            'price': price,
            'prev_close': prev_close,
            'open': float(parts[5]) if parts[5] else 0,
            'volume': int(parts[6]) if parts[6] else 0,
            'amount': float(parts[37]) * 10000 if parts[37] else 0,
            'high': high,
            'low': low,
            'change': float(parts[31]) if parts[31] else 0,
            'change_pct': float(parts[32]) if parts[32] else 0,
            'turnover': float(parts[38]) if parts[38] and parts[38] != '0' else None,
            'amplitude': amplitude,
            'pe_ttm': pe,
            'pe_dynamic': None,
            'market_cap': market_cap,
            'source': '腾讯行情',
        }
    except Exception:
        return None

def fetch_quote_sina(code):
    """新浪个股行情API（盘后可用，基本数据）"""
    prefix = 'sz' if code.startswith(('0','3')) else 'sh'
    url = f'https://hq.sinajs.cn/list={prefix}{code}'
    try:
        raw = _get(url, referer='https://finance.sina.com.cn/', decode='gb2312')
        m = re.search(rf'var hq_str_{prefix}{code}="([^"]+)"', raw)
        if not m: return None
        parts = m.group(1).split(',')
        # 格式: 名称, 今开, 昨收, 最新价, 最高, 最低, 日期, 时间, 成交量, 成交额, ...
        price = float(parts[3]) if parts[3] else 0
        prev_close = float(parts[2]) if parts[2] else 0
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
        return {
            'name': parts[0], 'code': code,
            'price': price,
            'open': float(parts[1]) if parts[1] else 0,
            'prev_close': prev_close,
            'high': float(parts[4]) if parts[4] else 0,
            'low': float(parts[5]) if parts[5] else 0,
            'volume': int(parts[8]) if len(parts) > 8 and parts[8] else 0,
            'amount': float(parts[9]) if len(parts) > 9 and parts[9] else 0,
            'change': round(price - prev_close, 2) if prev_close else 0,
            'change_pct': change_pct,
            'amplitude': round((float(parts[4]) - float(parts[5])) / prev_close * 100, 2) if parts[4] and parts[5] and prev_close else None,
            'turnover': None,
            'pe_ttm': None,
            'pe_dynamic': None,
            'market_cap': None,
            'source': '新浪行情',
        }
    except Exception:
        return None

def fetch_quote_with_fallback(code):
    """3层fallback获取行情：东方财富 → 腾讯 → 新浪"""
    warnings = []

    # 1. 东方财富（开盘时最优，盘后关闭）
    quote = fetch_quote_eastmoney(code)
    if quote: return quote, warnings

    # 2. 腾讯（盘后可用，数据丰富）
    quote = fetch_quote_tencent(code)
    if quote:
        warnings.append('东方财富行情API不可用（盘后关闭），已fallback到腾讯行情')
        return quote, warnings

    # 3. 新浪（盘后可用，基本数据）
    quote = fetch_quote_sina(code)
    if quote:
        warnings.append('东方财富和腾讯行情API不可用，已fallback到新浪行情（数据有限）')
        return quote, warnings

    return None, ['所有行情API均不可用']

# ═══════════════════════════════════════════════════════════
# 2. 财务数据 (新浪财经)
# ═══════════════════════════════════════════════════════════
def _parse_sina_financials(html):
    r = {}
    for var, key, cast in [
        ('a_totalcapital', 'total_shares', float),
        ('profit', 'profit_annual', float),
        ('profit_four', 'profit_4q', float),
        ('lastyear_mgsy', 'eps_annual', float),
        ('fourQ_mgsy', 'eps_4q', float),
        ('mgjzc', 'bps_latest', float),
    ]:
        m = re.search(rf'var {var}\s*=\s*([\d.]+)', html)
        if m: r[key] = cast(m.group(1))

    for label, key in [
        ('每股收益','eps'),('每股净资产','bps'),('净资产收益率','roe'),
        ('每股经营现金流净额','cfps'),('每股未分配利润','retained'),('每股公积金','capital_reserve'),
    ]:
        idx = html.find(f'<th>{label}</th>')
        if idx > 0:
            row_end = html.find('</tr>', idx)
            if row_end > 0:
                vals = re.findall(r'<td>([\d.-]+)(?:元|%)?</td>', html[idx:row_end])
                if len(vals) >= 5: r[key] = [float(v) for v in vals[:5]]

    periods = re.findall(r'<div class="tab">(\d{4}-\d{2})</div>', html)
    if periods: r['periods'] = periods
    divs = re.findall(r'(\d{4}-\d{2}-\d{2}).*?10派\s*(\d+\.?\d*)', html)
    if divs: r['dividends'] = [{'date': d[0], 'per10': float(d[1])} for d in divs[:3]]
    return r

def fetch_financials(code):
    prefix = 'sh' if code.startswith(('6','5')) else 'sz'
    url = f'https://finance.sina.com.cn/realstock/company/{prefix}{code}/nc.shtml'
    try:
        return _parse_sina_financials(_get(url, decode='gb2312', referer='https://finance.sina.com.cn/'))
    except Exception as e:
        return {'error': str(e)}

# ═══════════════════════════════════════════════════════════
# 3. K线数据 — 日线 (新浪 scale=240)
# ═══════════════════════════════════════════════════════════
def fetch_kline_daily(code, count=200):
    """获取日线K线数据。新浪 scale=240 表示日线。"""
    prefix = 'sh' if code.startswith(('6','5')) else 'sz'
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={count}'
    try:
        data = json.loads(_get(url, referer='https://finance.sina.com.cn/'))
        klines = []
        for d in data:
            klines.append({
                'date': d['day'],
                'open': float(d['open']), 'close': float(d['close']),
                'high': float(d['high']), 'low': float(d['low']),
                'volume': float(d['volume']),
            })
        return klines
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════
# 4. 主力资金流向 (东方财富资金流)
# ═══════════════════════════════════════════════════════════
def fetch_money_flow(code, days=5):
    """获取逐日主力资金流向。返回最近N天的净流入数据。"""
    url = f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={_secid(code)}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54&lmt={days}'
    try:
        d = json.loads(_get(url, referer='https://quote.eastmoney.com/'))
        if not d.get('data') or not d['data'].get('klines'):
            return None
        flows = []
        for line in d['data']['klines']:
            parts = line.split(',')
            # date, 主力净流入, 小单净流入, 中单净流入, 大单净流入, 超大单净流入
            date = parts[0]
            main_net = float(parts[1]) if len(parts) > 1 else 0
            small_net = float(parts[2]) if len(parts) > 2 else 0
            mid_net = float(parts[3]) if len(parts) > 3 else 0
            # 大单净流入 = 总净流入 - 小单净流入 - 中单净流入
            big_net = main_net - small_net - mid_net if main_net != 0 else 0
            flows.append({
                'date': date,
                'main_net': main_net,           # 主力净流入
                'big_net': big_net,             # 大单净流入
                'mid_net': mid_net,             # 中单净流入
                'small_net': small_net,         # 小单净流入
            })
        return flows
    except Exception:
        return None

def analyze_money_flow(flows):
    """分析资金流向趋势"""
    if not flows or len(flows) < 2:
        return {}
    total_main = sum(f['main_net'] for f in flows)
    recent_main = sum(f['main_net'] for f in flows[-3:])
    # 判断趋势
    if total_main > 0:
        total_verdict = '主力净流入'
    elif total_main < 0:
        total_verdict = '主力净流出'
    else:
        total_verdict = '主力持平'

    # 连续流入/流出天数
    consecutive_in = 0; consecutive_out = 0
    for f in reversed(flows):
        if f['main_net'] > 0: consecutive_in += 1
        else: break
    for f in reversed(flows):
        if f['main_net'] < 0: consecutive_out += 1
        else: break

    return {
        'total_main_net': total_main,
        'recent_3d_main_net': recent_main,
        'total_verdict': total_verdict,
        'consecutive_in_days': consecutive_in,
        'consecutive_out_days': consecutive_out,
        'daily_flows': flows,
    }

# ═══════════════════════════════════════════════════════════
# 5. 大盘指数行情 (新浪)
# ═══════════════════════════════════════════════════════════
def fetch_market_indices():
    """获取三大指数实时行情"""
    symbols = {
        's_sh000001': '上证指数',
        's_sz399001': '深证成指',
        's_sz399006': '创业板指',
    }
    url = f'https://hq.sinajs.cn/list={",".join(symbols.keys())}'
    try:
        raw = _get(url, referer='https://finance.sina.com.cn/', decode='gb2312')
        result = {}
        for sym, name in symbols.items():
            m = re.search(rf'var hq_str_{sym}="([^"]+)"', raw)
            if m:
                parts = m.group(1).split(',')
                # 格式: name, current_price, price_change, change_pct, volume, amount
                if len(parts) > 3:
                    cur_price = float(parts[1]) if parts[1] else 0
                    price_change = float(parts[2]) if parts[2] else 0
                    result[name] = {
                        'price': cur_price,
                        'change': price_change,
                        'change_pct': float(parts[3]) if parts[3] else 0,
                        'prev_close': round(cur_price - price_change, 2),
                    }
        return result
    except Exception:
        return {}

# ═══════════════════════════════════════════════════════════
# 6. 板块行情
# ═══════════════════════════════════════════════════════════
# 常见板块代码映射 (东方财富板块代码)
SECTOR_MAP = {
    '有色金属': '90.BK0478',
    '小金属': '90.BK0883',
    '稀土永磁': '90.BK0578',
    '半导体': '90.BK1036',
    '军工': '90.BK0590',
    '新材料': '90.BK0509',
    '碳纤维': '90.BK0937',
}

def fetch_sector(sector_name):
    """获取板块行情"""
    code = SECTOR_MAP.get(sector_name)
    if not code: return None
    url = f'https://push2.eastmoney.com/api/qt/stock/get?secid={code}&fields=f43,f57,f58,f169,f170'
    try:
        d = json.loads(_get(url, referer='https://quote.eastmoney.com/')).get('data')
        if not d: return None
        return {
            'name': d.get('f58',''),
            'price': round(d.get('f43',0)/100,2),
            'change_pct': round(d.get('f169',0)/100,2),
        }
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════
# 7. 技术指标计算
# ═══════════════════════════════════════════════════════════
def calc_technical(klines):
    if not klines or len(klines) < 20: return {}
    closes = [k['close'] for k in klines]
    volatiles = [k['high']-k['low'] for k in klines[-20:]]
    vols = [k['volume'] for k in klines]
    current = closes[-1]

    def ma(arr, n):
        if len(arr) < n: return None
        return round(sum(arr[-n:])/n, 2)

    mas = {}
    for n in [5,10,20,60,120,250]:
        m = ma(closes, n)
        if m: mas[f'ma{n}'] = m

    dev = {}
    for n in [5,20,60]:
        k = f'ma{n}'; dk = f'dev_ma{n}_pct'
        if k in mas:
            dev[dk] = round((current - mas[k]) / mas[k] * 100, 2)

    recent_60 = klines[-60:] if len(klines) >= 60 else klines
    high_60 = max(k['high'] for k in recent_60)
    low_60 = min(k['low'] for k in recent_60)
    high_20 = max(k['high'] for k in klines[-20:])
    low_20 = min(k['low'] for k in klines[-20:])

    c20, v20 = closes[-20:], vols[-20:]
    vwap = round(sum(c*v for c,v in zip(c20,v20))/sum(v20), 2) if sum(v20) > 0 else None

    # ATR(14)
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]['high'], klines[i]['low'], klines[i-1]['close']
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    atr_14 = round(sum(trs[-14:])/14, 2) if len(trs) >= 14 else None

    # RSI(14)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(d if d > 0 else 0)
        losses.append(-d if d < 0 else 0)
    avg_g = sum(gains[-14:])/14; avg_l = sum(losses[-14:])/14
    rsi = round(100 - (100/(1+avg_g/avg_l)), 1) if avg_l > 0 else 100

    # 区间位置
    pos_60 = round((current - low_60) / (high_60 - low_60) * 100, 1) if high_60 != low_60 else 50

    # 均线排列
    ma5, ma20, ma60 = mas.get('ma5'), mas.get('ma20'), mas.get('ma60')
    if ma5 and ma20 and ma60:
        if ma5 > ma20 > ma60: ma_status = '多头排列'
        elif ma5 < ma20 < ma60: ma_status = '空头排列'
        else: ma_status = '交叉缠绕'
    else:
        ma_status = '未知'

    # 近5日涨跌幅
    recent_pct = None
    if len(closes) >= 6:
        recent_pct = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2)

    return {
        **mas, **dev,
        'ma_status': ma_status,
        'high_60': high_60, 'low_60': low_60,
        'high_20': high_20, 'low_20': low_20,
        'vwap_20': vwap,
        'atr_14': atr_14,
        'rsi_14': rsi,
        'range_position_60_pct': pos_60,
        'avg_amplitude_20': round(sum(volatiles)/20, 2),
        'recent_5d_pct': recent_pct,
    }

def calc_volume(klines):
    if not klines or len(klines) < 20: return {}
    vols = [k['volume'] for k in klines]
    avg5 = sum(vols[-5:])/5; avg20 = sum(vols[-20:])/20
    today = klines[-1]['volume']
    ratio = round(today/avg5, 2) if avg5 > 0 else None
    if ratio is None: status = '未知'
    elif ratio > 2: status = '显著放量'
    elif ratio > 1.5: status = '放量'
    elif ratio > 0.8: status = '正常'
    elif ratio > 0.5: status = '缩量'
    else: status = '显著缩量'
    return {
        'avg_vol_5': round(avg5,0), 'avg_vol_20': round(avg20,0),
        'today_vol': today, 'vol_ratio': ratio, 'vol_status': status,
    }

# ═══════════════════════════════════════════════════════════
# 8. 估值计算
# ═══════════════════════════════════════════════════════════
def calc_valuation(quote, fin):
    r = {}
    price = quote.get('price', 0)
    eps_q = fin.get('eps', [])
    bps_q = fin.get('bps', [])
    eps_annual = fin.get('eps_annual')
    eps_4q = fin.get('eps_4q')

    if eps_4q and eps_4q > 0:
        r['pe_ttm'] = round(price / eps_4q, 1)
    elif eps_annual and eps_annual > 0:
        r['pe_ttm'] = round(price / eps_annual, 1)
    if eps_annual and eps_annual > 0:
        r['pe_annual'] = round(price / eps_annual, 1)
    if bps_q and len(bps_q) >= 1 and bps_q[0] > 0:
        r['pb'] = round(price / bps_q[0], 2)
    if eps_q and len(eps_q) >= 5 and bps_q and len(bps_q) >= 5:
        annual_eps = sum(eps_q[:4])
        avg_bps = (bps_q[0] + bps_q[3]) / 2
        if avg_bps > 0:
            r['roe_ttm'] = round(annual_eps / avg_bps * 100, 2)

    divs = fin.get('dividends', [])
    if divs and divs[0]['per10'] > 0:
        r['dividend_yield_pct'] = round(divs[0]['per10'] / 10 / price * 100, 2)
    if fin.get('profit_annual'): r['profit_annual_yi'] = fin['profit_annual']
    if fin.get('profit_4q'): r['profit_4q_yi'] = fin['profit_4q']
    return r

# ═══════════════════════════════════════════════════════════
# 8a. F10 深度数据 (东方财富F10 API — 盘后可用)
# ═══════════════════════════════════════════════════════════
def _fetch_f10_json(endpoint, code, timeout=10):
    """F10 API通用请求，处理UTF-8 BOM"""
    url = f'https://emweb.securities.eastmoney.com/PC_HSF10/{endpoint}/PageAjax?code=SZ{code}' if code.startswith(('0','3')) else \
          f'https://emweb.securities.eastmoney.com/PC_HSF10/{endpoint}/PageAjax?code=SH{code}'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'application/json',
            'Referer': 'https://emweb.securities.eastmoney.com/'
        })
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
        if raw[:3] == b'\xef\xbb\xbf': raw = raw[3:]  # strip UTF-8 BOM
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return None

def fetch_f10_shareholders(code):
    """获取股东人数变化 (F10 ShareholderResearch)"""
    data = _fetch_f10_json('ShareholderResearch', code)
    if not data: return None
    holders = data.get('gdrs', [])
    result = []
    for h in holders[:6]:  # 最近6期
        result.append({
            'date': h.get('END_DATE', '')[:10],
            'total_holders': h.get('HOLDER_TOTAL_NUM', 0),
            'avg_free_shares': h.get('AVG_FREE_SHARES', 0),
            'avg_hold_amount': round(h.get('AVG_HOLD_AMT', 0) / 10000, 1),  # 万元
            'hold_ratio_total': h.get('HOLD_RATIO_TOTAL', 0),
            'hold_focus': h.get('HOLD_FOCUS', ''),
        })
    return result

def fetch_f10_business(code):
    """获取主营业务构成 (F10 BusinessAnalysis — zygcfx)"""
    data = _fetch_f10_json('BusinessAnalysis', code)
    if not data: return None
    result = {}
    # zygcfx = 主营业务构成分析 (按产品/行业)
    items = data.get('zygcfx', [])
    if items:
        result['main_business'] = []
        seen = set()
        for item in items:
            name = item.get('ITEM_NAME', '')
            # 取最新报告期的数据，去重
            report_date = item.get('REPORT_DATE', '')[:10]
            if name not in seen:
                seen.add(name)
                # MAIN_BUSINESS_RPOFIT 是利润金额（元），东方财富命名的 RPOFIT 是拼写错误
                profit_amt = round(item.get('MAIN_BUSINESS_RPOFIT', 0) / 1e8, 2)
                result['main_business'].append({
                    'date': report_date,
                    'type': item.get('MAINOP_TYPE', ''),
                    'name': name,
                    'revenue': round(item.get('MAIN_BUSINESS_INCOME', 0) / 1e8, 2),
                    'revenue_pct': item.get('MBI_RATIO', 0),
                    'profit_amt': profit_amt,  # 利润金额（亿）
                })
            if len(result['main_business']) >= 5:
                break
        # 按营收占比排序
        result['main_business'].sort(key=lambda x: x['revenue'], reverse=True)
    return result

# ═══════════════════════════════════════════════════════════
# 8b. 龙虎榜数据 (东方财富 datacenter — 盘后可用)
# ═══════════════════════════════════════════════════════════
def fetch_dragon_tiger(code, days=10):
    """获取最近龙虎榜上榜记录"""
    filter_str = f'(SECURITY_CODE=%22{code}%22)'
    url = f'https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=TRADE_DATE&sortTypes=-1&pageSize={days}&pageNumber=1&reportName=RPT_DAILYBILLBOARD_DETAILS&columns=ALL&filter={filter_str}'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'application/json',
            'Referer': 'https://data.eastmoney.com/stock/tradedetail/'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('utf-8'))
        items = data.get('result', {}).get('data', [])
        if not items: return None
        result = []
        for item in items:
            result.append({
                'date': item.get('TRADE_DATE', '')[:10],
                'reason': item.get('EXPLANATION', ''),
                'explain': item.get('EXPLAIN', ''),
                'close': item.get('CLOSE_PRICE', 0),
                'change_pct': item.get('CHANGE_RATE', 0),
                'buy_amt': round(item.get('BILLBOARD_BUY_AMT', 0) / 1e8, 2),
                'sell_amt': round(item.get('BILLBOARD_SELL_AMT', 0) / 1e8, 2),
                'net_amt': round(item.get('BILLBOARD_NET_AMT', 0) / 1e8, 2),
                'deal_amt': round(item.get('BILLBOARD_DEAL_AMT', 0) / 1e8, 2),
                'turnover_rate': item.get('TURNOVERRATE', 0),
                'buy_ratio': item.get('BUY_RATIO', 0),
                'sell_ratio': item.get('SELL_RATIO', 0),
                'trade_market': item.get('TRADE_MARKET', ''),
            })
        return result
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════
# 8c. 个股新闻 (新浪财经 — 盘后可用)
# ═══════════════════════════════════════════════════════════
def fetch_news(code, count=8):
    """获取个股新闻 (新浪财经个股新闻页面，盘后可用)"""
    prefix = 'sz' if code.startswith(('0','3')) else 'sh'
    url = f'https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{prefix}{code}.phtml'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer': 'https://finance.sina.com.cn/'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode('gb2312', errors='ignore')
        # 从页面中提取股票名称
        name_match = re.search(r'<title>(\w+?)\(', raw)
        stock_name = name_match.group(1) if name_match else ''
        # 提取日期+时间+链接+标题: &nbsp;YYYY-MM-DD&nbsp;HH:MM&nbsp;&nbsp;<a href='...'>title</a>
        pattern = r"(\d{4}-\d{2}-\d{2})\D*?(\d{2}:\d{2})\D*?<a[^>]*?href='([^']+)'[^>]*?>([^<]+)</a>"
        matches = re.findall(pattern, raw)
        result = []
        for d, t, href, title in matches:
            title = title.strip()
            if not title or len(title) < 5:
                continue
            # 过滤：标题必须包含股票名称或代码，排除全局新闻
            if stock_name and stock_name not in title and code not in title:
                continue
            result.append({'title': title, 'date': f'{d} {t}', 'url': href})
            if len(result) >= count:
                break
        return result if result else None
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════
# 9. 综合编排 (v3.0: 解耦架构 + 舆情/龙虎榜/F10数据)
# ═══════════════════════════════════════════════════════════
def analyze(code, sectors=None):
    """sectors: 可选，要查询的板块列表，如 ['有色金属','小金属']"""
    result = {'code': code, 'fetch_time': time.strftime('%Y-%m-%d %H:%M:%S'), 'warnings': []}

    # 行情（3层fallback: 东方财富 → 腾讯 → 新浪）
    quote, quote_warnings = fetch_quote_with_fallback(code)
    result['warnings'].extend(quote_warnings)
    if not quote:
        result['error'] = '所有行情API均不可用'
        return result
    result['quote'] = quote

    # 财务（独立，新浪API盘后可用）
    try:
        result['financials'] = fetch_financials(code)
    except Exception:
        result['financials'] = {'error': '财务数据获取失败'}
        result['warnings'].append('财务数据获取失败')

    # K线-日线（独立，新浪API盘后可用）
    try:
        klines = fetch_kline_daily(code)
        if klines:
            result['kline_count'] = len(klines)
            result['kline_latest'] = klines[-1]
            result['kline_range'] = f'{klines[0]["date"]} ~ {klines[-1]["date"]}'
            result['technical'] = calc_technical(klines)
            result['volume'] = calc_volume(klines)
        else:
            result['warnings'].append('K线数据获取失败')
    except Exception:
        result['warnings'].append('K线数据获取失败')

    # 主力资金流向（独立，东方财富API盘后关闭）
    try:
        flows = fetch_money_flow(code, days=5)
        if flows:
            result['money_flow'] = analyze_money_flow(flows)
        else:
            result['warnings'].append('主力资金流向数据不可用（盘后关闭）')
    except Exception:
        result['warnings'].append('主力资金流向数据不可用')

    # 估值（独立计算，依赖行情和财务数据）
    try:
        result['valuation'] = calc_valuation(quote, result.get('financials', {}))
    except Exception:
        result['valuation'] = {}
        result['warnings'].append('估值计算失败')

    # 大盘指数（独立，新浪API盘后可用）
    try:
        result['market_indices'] = fetch_market_indices()
    except Exception:
        result['market_indices'] = {}
        result['warnings'].append('大盘指数获取失败')

    # 板块行情（独立，东方财富API盘后关闭）
    if sectors:
        try:
            result['sectors'] = {}
            for s in sectors:
                sec = fetch_sector(s)
                if sec: result['sectors'][s] = sec
        except Exception:
            result['warnings'].append('板块行情获取失败')

    # P1: 个股新闻/公告（东方财富公告API，盘后可用）
    try:
        news = fetch_news(code, count=8)
        if news:
            result['news'] = news
    except Exception:
        result['warnings'].append('个股新闻获取失败')

    # P1/P2: 龙虎榜（东方财富datacenter，盘后可用）
    try:
        dt = fetch_dragon_tiger(code, days=10)
        if dt:
            result['dragon_tiger'] = dt
    except Exception:
        pass  # 龙虎榜数据不是每个股票都有，静默跳过

    # P2: F10股东人数（东方财富F10，盘后可用）
    try:
        holders = fetch_f10_shareholders(code)
        if holders:
            result['f10_shareholders'] = holders
    except Exception:
        pass

    # P2: F10主营业务（东方财富F10，盘后可用）
    try:
        biz = fetch_f10_business(code)
        if biz:
            result['f10_business'] = biz
    except Exception:
        pass

    return result

# ═══════════════════════════════════════════════════════════
# 10. CLI
# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python fetch_stock_data.py <code> [--json]")
        print("  数据维度: 行情(3层fallback) | 财务 | K线(日线) | 主力资金 | 大盘指数 | 板块")
        print("  v3.0新增: 个股新闻 | 龙虎榜 | 股东人数变化 | 主营业务构成")
        sys.exit(1)

    code = sys.argv[1]
    data = analyze(code)

    if '--json' in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        sys.exit(0)

    if 'error' in data:
        print(f"ERROR: {data['error']}")
        # v3.0: 即使有error也尝试输出已获取的数据（如K线、财务可能仍可用）
        has_partial = any(k in data for k in ['financials','technical','kline_count'])
        if not has_partial:
            sys.exit(1)
        print("⚠️ 以下为部分可用数据，请谨慎使用\n")

    q = data.get('quote', {})
    f = data.get('financials', {})
    v = data.get('valuation', {})
    t = data.get('technical', {})
    vol = data.get('volume', {})
    mf = data.get('money_flow', {})
    idx = data.get('market_indices', {})

    def p(x): print(x)

    p(f"\n{'═'*65}")
    p(f"  {q.get('name','')} ({q.get('code','')})  日线分析")
    p(f"  数据时间: {data['fetch_time']}  K线: {data.get('kline_range','无')}")
    p(f"  行情来源: {q.get('source','未知')}")
    p(f"{'═'*65}")

    # 警告信息
    warnings = data.get('warnings', [])
    if warnings:
        p("\n┌─ ⚠️ 数据缺失提示 ──────────────────────────")
        for w in warnings:
            p(f"│  ⚡ {w}")
        p(f"└──────────────────────────────────────────")

    # 大盘环境
    if idx:
        p("\n┌─ 大盘环境 ─────────────────────────────────")
        for name in ['上证指数','深证成指','创业板指']:
            if name in idx:
                i = idx[name]
                p(f"│ {name}: {i['price']:.0f}  {i['change_pct']:+.2f}%")
        p(f"└──────────────────────────────────────────")

    # 行情
    p("\n┌─ 行情 ─────────────────────────────────────")
    p(f"│ 价格: {q.get('price')}  涨跌: {q.get('change_pct'):+.2f}%  振幅: {q.get('amplitude')}%")
    p(f"│ 今开: {q.get('open')}  最高: {q.get('high')}  最低: {q.get('low')}  昨收: {q.get('prev_close')}")
    p(f"│ 总市值: {q.get('market_cap')}亿  PE(TTM): {q.get('pe_ttm')}x")
    p(f"└──────────────────────────────────────────")

    # 财务
    p("\n┌─ 财务数据 ────────────────────────────────")
    if f.get('total_shares'):   p(f"│ 总股本: {f['total_shares']:.0f}万股")
    if f.get('eps'):             p(f"│ EPS(季度): {f['eps']}")
    if f.get('eps_annual'):      p(f"│ EPS(年度): {f['eps_annual']}")
    if f.get('eps_4q'):          p(f"│ EPS(4Q合计): {f['eps_4q']}")
    if f.get('bps'):             p(f"│ BPS(季度): {f['bps']}")
    if f.get('roe'):             p(f"│ ROE(季度): {f['roe']}%")
    if f.get('profit_annual'):   p(f"│ 年度净利润: {f['profit_annual']}亿")
    if f.get('periods'):         p(f"│ 报告期: {f['periods']}")
    if f.get('dividends'):       p(f"│ 分红: {f['dividends']}")
    p(f"└──────────────────────────────────────────")

    # 估值
    p("\n┌─ 估值分析 ────────────────────────────────")
    for k, label in [('pe_ttm','PE(TTM)'),('pe_annual','PE(年度)'),('pb','PB'),('roe_ttm','ROE(TTM)%'),('dividend_yield_pct','股息率%'),('profit_annual_yi','年利润(亿)'),('profit_4q_yi','4Q利润(亿)')]:
        if k in v: p(f"│ {label}: {v[k]}")
    p(f"└──────────────────────────────────────────")

    # 技术面
    p("\n┌─ 技术面 (日线) ───────────────────────────")
    p(f"│ 均线状态: {t.get('ma_status','未知')}")
    for n in [5,10,20,60,120,250]:
        k = f'ma{n}'; dk = f'dev_ma{n}_pct'
        if k in t:
            line = f"│ MA{n}: {t[k]}"
            if dk in t: line += f"  (偏离: {t[dk]:+.1f}%)"
            p(line)
    if t.get('high_60'): p(f"│ 60日高: {t['high_60']}  60日低: {t['low_60']}")
    if t.get('high_20'): p(f"│ 20日高: {t['high_20']}  20日低: {t['low_20']}")
    if t.get('vwap_20'): p(f"│ 20日VWAP: {t['vwap_20']}")
    if t.get('rsi_14') is not None: p(f"│ RSI(14): {t['rsi_14']}")
    if t.get('atr_14'): p(f"│ ATR(14): {t['atr_14']}")
    if t.get('range_position_60_pct') is not None:
        p(f"│ 60日区间位置: {t['range_position_60_pct']}%")
    if t.get('recent_5d_pct') is not None:
        p(f"│ 近5日涨跌: {t['recent_5d_pct']:+.2f}%")
    p(f"└──────────────────────────────────────────")

    # 成交量
    p("\n┌─ 成交量 ──────────────────────────────────")
    if vol.get('vol_ratio'): p(f"│ 量比: {vol['vol_ratio']}  ({vol.get('vol_status','')})")
    if vol.get('avg_vol_5'): p(f"│ 5日均量: {vol['avg_vol_5']:.0f}手")
    if vol.get('avg_vol_20'): p(f"│ 20日均量: {vol['avg_vol_20']:.0f}手")
    p(f"└──────────────────────────────────────────")

    # 资金流向
    if mf and mf.get('daily_flows'):
        p("\n┌─ 主力资金流向 (近5日) ───────────────────")
        p(f"│ 总净流入: {mf['total_main_net']/1e8:+.2f}亿")
        p(f"│ 近3日净流入: {mf['recent_3d_main_net']/1e8:+.2f}亿")
        p(f"│ 综合判断: {mf['total_verdict']}")
        if mf.get('consecutive_out_days'): p(f"│ 连续流出: {mf['consecutive_out_days']}天")
        if mf.get('consecutive_in_days'): p(f"│ 连续流入: {mf['consecutive_in_days']}天")
        p(f"│ 逐日明细:")
        for f in mf['daily_flows']:
            p(f"│   {f['date']}  主力: {f['main_net']/1e8:+.2f}亿  |  大单: {f['big_net']/1e8:+.2f}亿  中单: {f['mid_net']/1e8:+.2f}亿  小单: {f['small_net']/1e8:+.2f}亿")
        p(f"└──────────────────────────────────────────")
    else:
        p("\n┌─ 主力资金流向 ────────────────────────────")
        p(f"│ ⚡ 资金流向数据不可用（盘后API关闭）")
        p(f"└──────────────────────────────────────────")

    # P1: 个股新闻
    news = data.get('news', [])
    if news:
        p("\n┌─ 近期新闻 ────────────────────────────────")
        for n in news[:6]:
            title = n.get('title','')[:55]
            date = n.get('date','')[:10]
            p(f"│ [{date}] {title}")
        p(f"└──────────────────────────────────────────")

    # P1/P2: 龙虎榜
    dt = data.get('dragon_tiger', [])
    if dt:
        p("\n┌─ 龙虎榜 (近10日) ─────────────────────────")
        for d in dt[:5]:
            p(f"│ {d['date']} {d.get('reason','')}")
            p(f"│   买入:{d['buy_amt']:+.2f}亿 卖出:{d['sell_amt']:+.2f}亿 净额:{d['net_amt']:+.2f}亿")
            p(f"│   涨幅:{d['change_pct']:+.1f}% 换手:{d.get('turnover_rate',0)}% 机构买:{d.get('buy_org_count',0)}家 卖:{d.get('sell_org_count',0)}家")
        p(f"└──────────────────────────────────────────")

    # P2: F10股东人数变化
    holders = data.get('f10_shareholders', [])
    if holders and len(holders) >= 2:
        p("\n┌─ 股东人数变化 ────────────────────────────")
        latest = holders[0]
        prev = holders[1] if len(holders) > 1 else None
        p(f"│ 最新: {latest['date']} — {latest['total_holders']}户 │ 户均持股市值:{latest['avg_hold_amount']}万│ 筹码:{latest['hold_focus']}")
        if prev:
            change = (latest['total_holders'] - prev['total_holders']) / prev['total_holders'] * 100
            p(f"│ 上期: {prev['date']} — {prev['total_holders']}户 │ 变化: {change:+.1f}% {'(筹码集中)' if change < 0 else '(筹码分散)'}")
        # 最近几期趋势
        if len(holders) >= 3:
            trend = []
            for h in holders[:5]:
                trend.append(str(h['total_holders']))
            p(f"│ 近5期: {' → '.join(trend)}")
        p(f"└──────────────────────────────────────────")

    # P2: F10主营业务
    biz = data.get('f10_business', {})
    if biz and biz.get('main_business'):
        p("\n┌─ 主营业务构成 ────────────────────────────")
        for item in biz['main_business'][:5]:
            p(f"│ {item['name'][:30]} — 营收:{item['revenue']}亿({item['revenue_pct']}%) 利润:{item['profit_amt']}亿")
        p(f"└──────────────────────────────────────────")

    p(f"\n{'═'*65}")
    p(f"  数据获取完毕。请交由 AI 进行深度分析。")
    p(f"{'═'*65}\n")