"""
BTC 三层共振信号库 - 数据采集层
================================
每个采集函数返回单一 float（指标当前值），失败返回 None 并打印告警，
绝不抛异常中断全局——某个源挂了，其余指标照常更新。

数据源全部免费：
  FRED          - 联邦基金利率（需免费 API key，存 GitHub Secret）
  Alternative.me- 恐惧贪婪指数（无需 key）
  CoinGecko     - BTC 历史价格（自算 Mayer / Pi Cycle / 200周线）
  OKX           - Funding Rate（对地域比 Binance 宽松）
  DefiLlama     - 稳定币市值（备用）

环境变量（GitHub Secrets）：
  FRED_API_KEY   - FRED 免费 key（https://fred.stlouisfed.org/docs/api/api_key.html）
"""

import os
import time
import requests

TIMEOUT = 15
HEADERS = {"User-Agent": "btc-signals-bot/1.0"}


def _get(url, params=None, headers=None, retries=2):
    """带重试的 GET，失败返回 None"""
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            print(f"  [warn] {url} -> HTTP {r.status_code}")
        except Exception as e:
            print(f"  [warn] {url} -> {e}")
        time.sleep(1.5 * (attempt + 1))
    return None


# ============================================================
# 价格数据缓存（多个指标共用 BTC 历史价，只拉一次）
# ============================================================
_PRICE_CACHE = {"daily": None}


def _btc_daily_prices(days=1460):
    """
    CoinGecko BTC 日线收盘价，返回 [price, ...] 旧->新。
    用于自算 Mayer / Pi Cycle / 200周线。缓存避免重复请求。
    """
    if _PRICE_CACHE["daily"] is not None:
        return _PRICE_CACHE["daily"]
    r = _get(
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": str(days), "interval": "daily"},
    )
    if r is None:
        return None
    try:
        prices = [p[1] for p in r.json()["prices"]]
        _PRICE_CACHE["daily"] = prices
        return prices
    except Exception as e:
        print(f"  [warn] price parse: {e}")
        return None


def _sma(values, n):
    if not values or len(values) < n:
        return None
    return sum(values[-n:]) / n


# ============================================================
# 采集函数
# ============================================================
def fred_ffr():
    """联邦基金利率最新值（%）"""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("  [skip] FRED_API_KEY 未设置")
        return None
    r = _get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": "FEDFUNDS", "api_key": key, "file_type": "json",
            "sort_order": "desc", "limit": 1,
        },
    )
    if r is None:
        return None
    try:
        return float(r.json()["observations"][0]["value"])
    except Exception as e:
        print(f"  [warn] FFR parse: {e}")
        return None


def fear_greed():
    """恐惧贪婪指数 0-100"""
    r = _get("https://api.alternative.me/fng/?limit=1")
    if r is None:
        return None
    try:
        return float(r.json()["data"][0]["value"])
    except Exception as e:
        print(f"  [warn] F&G parse: {e}")
        return None


def mayer_multiple():
    """Mayer = 现价 / 200日均线"""
    prices = _btc_daily_prices()
    if not prices:
        return None
    ma200 = _sma(prices, 200)
    if not ma200:
        return None
    return prices[-1] / ma200


def pi_cycle():
    """
    Pi Cycle 比值 = 111DMA / (2 * 350DMA)。
    >=1 表示 111DMA 上穿 2x350DMA = 周期顶。
    """
    prices = _btc_daily_prices()
    if not prices:
        return None
    dma111 = _sma(prices, 111)
    dma350 = _sma(prices, 350)
    if not dma111 or not dma350:
        return None
    return dma111 / (2 * dma350)


def ma200w_mult():
    """价格 / 200周均线（=1400日均线）"""
    prices = _btc_daily_prices()
    if not prices:
        return None
    ma = _sma(prices, 1400)
    if not ma:
        return None
    return prices[-1] / ma


def funding_rate():
    """
    BTC 永续资金费率（%）。优先 OKX（地域宽松），失败回退 CoinGecko 衍生品。
    返回百分比数值，如 0.01 表示 0.01%。
    """
    # OKX
    r = _get(
        "https://www.okx.com/api/v5/public/funding-rate",
        params={"instId": "BTC-USDT-SWAP"},
    )
    if r is not None:
        try:
            return float(r.json()["data"][0]["fundingRate"]) * 100.0
        except Exception as e:
            print(f"  [warn] OKX funding parse: {e}")
    return None


def stablecoin_mcap():
    """
    稳定币供应信号 = 当前总市值相对 90 日均值的偏离百分比。
    DefiLlama 免费历史端点 /stablecoincharts/all。

    返回偏离度（%）：
      正值 = 当前供应高于90日均值 = 稳定币扩张 = 资金入场（偏多/偏热）
      负值 = 收缩 = 资金流出（偏冷）
    返回 None 表示采集失败。

    注：返回的是"偏离度"而非绝对市值，因为绝对值无"距顶"含义；
    偏离度才能映射成方向性信号。归一化在 config 的 progress 规则里完成。
    """
    r = _get("https://stablecoins.llama.fi/stablecoincharts/all")
    if r is None:
        return None
    try:
        data = r.json()
        if not isinstance(data, list) or len(data) < 90:
            print("  [warn] stablecoin: 历史数据不足90天")
            return None
        # 提取每日总市值序列（字段名兜底）
        series = []
        for row in data:
            v = row.get("totalCirculatingUSD")
            if isinstance(v, dict):
                val = v.get("peggedUSD")
            else:
                val = v
            if val is not None:
                try:
                    series.append(float(val))
                except (ValueError, TypeError):
                    pass
        if len(series) < 90:
            return None
        current = series[-1]
        ma90 = sum(series[-90:]) / 90
        if ma90 == 0:
            return None
        deviation = (current - ma90) / ma90 * 100.0
        return deviation
    except Exception as e:
        print(f"  [warn] stablecoin parse: {e}")
        return None


# ============================================================
# Coin Metrics 社区版底层字段（已验证社区版可用）
#   CapMrktCurUSD = 流通市值 | CapRealUSD = 已实现市值 | IssuanceUSD = 日矿工产出
# 用于自算 MVRV Z / NUPL / Puell
# ============================================================
_CM_CACHE = {}


def _cm_timeseries(metric, page_size=10000):
    """
    拉取 Coin Metrics 社区版某指标的完整日线序列，返回 [(date, float), ...] 旧->新。
    社区版无需 key。带缓存，多个指标复用同次请求。
    """
    if metric in _CM_CACHE:
        return _CM_CACHE[metric]
    url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    out = []
    params = {
        "assets": "btc", "metrics": metric,
        "frequency": "1d", "page_size": str(page_size),
    }
    next_url = url
    next_params = params
    for _ in range(6):  # 最多翻 6 页，足够覆盖多年日线
        r = _get(next_url, params=next_params)
        if r is None:
            break
        try:
            j = r.json()
        except Exception as e:
            print(f"  [warn] CM {metric} parse: {e}")
            break
        for row in j.get("data", []):
            v = row.get(metric)
            if v is not None:
                try:
                    out.append((row["time"], float(v)))
                except (ValueError, TypeError):
                    pass
        nxt = j.get("next_page_url")
        if nxt:
            next_url, next_params = nxt, None  # next_page_url 已含全部参数
        else:
            break
    _CM_CACHE[metric] = out
    return out


def _cm_latest(metric):
    """取某 CM 指标最新值"""
    series = _cm_timeseries(metric)
    return series[-1][1] if series else None


def mvrv_zscore():
    """
    MVRV Z-score = (市值 - 已实现市值) / 市值序列的标准差
    自算，社区版字段 CapMrktCurUSD / CapRealUSD。
    """
    mcap = _cm_timeseries("CapMrktCurUSD")
    rcap = _cm_timeseries("CapRealUSD")
    if not mcap or not rcap:
        return None
    # 对齐到相同日期
    rcap_map = dict(rcap)
    diffs = []
    latest_diff = None
    for t, m in mcap:
        if t in rcap_map:
            d = m - rcap_map[t]
            diffs.append(d)
            latest_diff = d
    if not diffs or latest_diff is None:
        return None
    # 标准差（总体）
    n = len(diffs)
    mean = sum(diffs) / n
    var = sum((x - mean) ** 2 for x in diffs) / n
    std = var ** 0.5
    if std == 0:
        return None
    return latest_diff / std


def nupl():
    """
    NUPL = (市值 - 已实现市值) / 市值。社区版字段自算。
    """
    mcap = _cm_latest("CapMrktCurUSD")
    rcap = _cm_latest("CapRealUSD")
    if mcap is None or rcap is None or mcap == 0:
        return None
    return (mcap - rcap) / mcap


def puell_multiple():
    """
    Puell Multiple = 当日 IssuanceUSD / 过去365日 IssuanceUSD 均值。
    社区版字段 IssuanceUSD，自算，与 Glassnode 公式完全一致。
    """
    series = _cm_timeseries("IssuanceUSD")
    if not series or len(series) < 365:
        return None
    values = [v for _, v in series]
    today = values[-1]
    ma365 = sum(values[-365:]) / 365
    if ma365 == 0:
        return None
    return today / ma365


# ============================================================
# BGeometrics API（官方直出指标，token 走环境变量）
#   端点格式: https://api.bgeometrics.com/v1/{slug}?token=XXX
#   或 header: Authorization: Bearer XXX
#   返回结构: {"d":"2026-06-14","unixTs":...,"<slug>":<value>}
#   字段名 == slug 本身（去掉连字符前的规律：值键就是 slug 原文）
#   免费档限额: 8次/小时, 15次/天 —— 每天跑1次绰绰有余
# ============================================================
BGAPI_BASE = "https://api.bgeometrics.com/v1"


def _bg_get(slug, value_key=None):
    """
    拉取 BGeometrics 某指标最新值。
    value_key 默认等于 slug（BG 的字段名规律）；个别指标可显式指定。
    token 从环境变量 BGAPI_TOKEN 读取，放 Authorization header（不进 URL）。
    """
    token = os.environ.get("BGAPI_TOKEN")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = _get(f"{BGAPI_BASE}/{slug}", headers=headers)
    if r is None:
        return None
    try:
        data = r.json()
        # BG 返回可能是单对象或列表，统一取最后一条
        if isinstance(data, list):
            if not data:
                return None
            obj = data[-1]
        else:
            obj = data
        key = value_key or slug
        # 优先精确键，回退到对象里第一个非元数据字段
        if key in obj:
            return float(obj[key])
        for k, v in obj.items():
            if k not in ("d", "unixTs") and isinstance(v, (int, float)):
                return float(v)
        return None
    except Exception as e:
        print(f"  [warn] BG {slug} parse: {e}")
        return None


def bg_sopr():
    """SOPR（官方直出，自算不可行）"""
    return _bg_get("sopr")


def bg_mvrv_zscore():
    """MVRV Z-score（官方直出，比自算更准更省）"""
    return _bg_get("mvrv-zscore")


def bg_nupl():
    """NUPL（官方直出）"""
    return _bg_get("nupl")


def bg_puell():
    """Puell Multiple（官方直出）"""
    return _bg_get("puell-multiple")


def bg_funding_rate():
    """Funding Rate（BG 源，规避交易所地域限制）"""
    return _bg_get("funding-rate")


def bg_fear_greed():
    """恐惧贪婪指数（BG 源）"""
    return _bg_get("fear-greed")


def bg_reserve_risk():
    """Reserve Risk（顶部/底部辅助指标，可选新增）"""
    return _bg_get("reserve-risk")


def coinbase_premium():
    """
    Coinbase 溢价% = (Coinbase价 - Binance价) / Binance价 * 100。
    地域受限时返回 None。
    """
    cb = _get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
    if cb is None:
        return None
    try:
        cb_price = float(cb.json()["price"])
    except Exception:
        return None
    # 用 CoinGecko 现价作为基准对照（避开 Binance 地域问题）
    cg = _get("https://api.coingecko.com/api/v3/simple/price",
              params={"ids": "bitcoin", "vs_currencies": "usd"})
    if cg is None:
        return None
    try:
        base = float(cg.json()["bitcoin"]["usd"])
        return (cb_price - base) / base * 100.0
    except Exception:
        return None


# 采集器注册表：config.py 里的 source 字段 -> 函数
FETCHERS = {
    # 免费源 / 自算
    "fred_ffr": fred_ffr,
    "fear_greed": fear_greed,
    "mayer_multiple": mayer_multiple,
    "pi_cycle": pi_cycle,
    "ma200w_mult": ma200w_mult,
    "funding_rate": funding_rate,
    "stablecoin_mcap": stablecoin_mcap,
    "coinbase_premium": coinbase_premium,
    "mvrv_zscore": mvrv_zscore,
    "nupl": nupl,
    "puell_multiple": puell_multiple,
    # BGeometrics 官方直出（需 BGAPI_TOKEN，优先级高于自算）
    "bg_sopr": bg_sopr,
    "bg_mvrv_zscore": bg_mvrv_zscore,
    "bg_nupl": bg_nupl,
    "bg_puell": bg_puell,
    "bg_funding_rate": bg_funding_rate,
    "bg_fear_greed": bg_fear_greed,
    "bg_reserve_risk": bg_reserve_risk,
}
