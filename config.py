"""
BTC 三层共振信号库 - 配置文件
================================
集中管理：指标定义、进度归一化规则、Notion 页面 ID 映射。
调整阈值或新增指标只改这里，不动主脚本逻辑。

进度口径统一定义为「距顶部进度 0-100」（与 CoinGlass 一致）：
  - 进度 = 当前值相对「中性下沿 → 顶部阈值」区间的百分比位置
  - 100 = 已达顶部阈值（极热/逃顶）
  -   0 = 在中性下沿或更冷（极冷/抄底侧）
  - 反向指标（值越低越接近顶）由各自的 progress 函数内部处理方向

档位映射（在 Notion 公式列里完成，这里只写回「当前进度」数字）：
  >=80 -> +2 | 60-80 -> +1 | 40-60 -> 0 | 20-40 -> -1 | <20 -> -2
"""

# ============================================================
# Notion 数据源（collection）ID —— 写回时定位每一行
# ============================================================
NOTION_DATA_SOURCE_ID = "6a7ace0c-8bd5-483c-9cc4-de60fb3a4ee1"

# Notion 数据库 page_id（用于查询所有行）
NOTION_DATABASE_ID = "767ed855-e477-453f-a1fe-7b10525f9c31"

# Notion API 版本
NOTION_VERSION = "2022-06-28"


# ============================================================
# 进度归一化辅助函数
# ============================================================
def clamp(x, lo=0.0, hi=100.0):
    """把进度限制在 0-100 之间"""
    return max(lo, min(hi, x))


def progress_up(value, neutral_low, top_threshold):
    """
    正向指标：值越高越接近顶部。
    例：MVRV Z-score，neutral_low=0，top=7 -> 当前0.37 约等于 5%
    """
    if value is None:
        return None
    span = top_threshold - neutral_low
    if span == 0:
        return None
    return clamp((value - neutral_low) / span * 100.0)


def progress_down(value, neutral_high, top_threshold):
    """
    反向指标：值越低越接近顶部。
    例：ETF占比参考<=3.5%，当前11.28% -> 离顶还远，进度低
    neutral_high 是「最冷」侧的高值，top_threshold 是顶部（低值）
    """
    if value is None:
        return None
    span = neutral_high - top_threshold
    if span == 0:
        return None
    return clamp((neutral_high - value) / span * 100.0)


# ============================================================
# 指标注册表
# ============================================================
# 每个指标：
#   key        : 内部标识
#   notion_name: Notion 中「指标」标题（用于匹配行）
#   source     : 采集器函数名（见 fetch_signals.py 的 FETCHERS）
#   progress   : (fn, *args) 进度计算；fn 接收采集到的原始值 value
#   enabled    : 是否纳入自动化（False = 留手填）
#
# 注意：链上估值类（MVRV/NUPL/SOPR/Puell/RHODL）在路线A下无免费权威源，
#       标 enabled=False，保持手填，待路线B/C再开启。

INDICATORS = [
    # ---------- ① 价格趋势 ----------
    {
        "key": "mayer",
        "notion_name": "Mayer Multiple",
        "source": "mayer_multiple",
        "progress": ("progress_up", 0.8, 2.4),   # 中性下沿0.8，顶部2.4
        "enabled": True,
    },
    {
        "key": "pi_cycle",
        "notion_name": "Pi Cycle Top",
        "source": "pi_cycle",
        "progress": ("progress_up", 0.0, 1.0),    # 比值 111DMA/(2*350DMA)，>=1 触顶
        "enabled": True,
    },
    {
        "key": "ma200w",
        "notion_name": "200周均线偏离",
        "source": "ma200w_mult",
        "progress": ("progress_up", 1.0, 5.0),     # 价格/200周线，参考彩虹顶约5x
        "enabled": True,
    },

    # ---------- ② 链上估值（BGeometrics 官方直出，优于自算）----------
    {"key": "mvrv", "notion_name": "MVRV Z-score", "source": "bg_mvrv_zscore",
     "progress": ("progress_up", 0.0, 7.0), "enabled": True},
    {"key": "nupl", "notion_name": "NUPL 净未实现盈亏", "source": "bg_nupl",
     "progress": ("progress_up", 0.0, 0.75), "enabled": True},
    {"key": "puell", "notion_name": "Puell Multiple", "source": "bg_puell",
     "progress": ("progress_up", 0.5, 4.0), "enabled": True},
    # SOPR：BGeometrics 官方直出，自算不可行的问题已解决
    # 进度区间 0.97(熊底投降)→1.04(牛顶获利了结)，当前1.001约46%(中性)
    {"key": "sopr", "notion_name": "SOPR", "source": "bg_sopr",
     "progress": ("progress_up", 0.97, 1.04), "enabled": True},

    # ---------- ③ 资金流动性 ----------
    {
        "key": "ffr",
        "notion_name": "联邦基金利率 FFR",
        "source": "fred_ffr",
        # FFR 是宏观锚，方向特殊：加息(高)=利空(偏顶/热)，降息(低)=利多(偏冷/底)
        # 用 progress_up：利率越高越"热"。中性约2%，顶部约5.5%
        "progress": ("progress_up", 2.0, 5.5),
        "enabled": True,
    },
    {
        "key": "fear_greed",
        "notion_name": "恐惧贪婪指数",
        "source": "bg_fear_greed",
        "progress": ("progress_up", 20.0, 80.0),   # 20恐惧 80贪婪
        "enabled": True,
    },
    {
        "key": "stablecoin",
        "notion_name": "稳定币供应",
        "source": "stablecoin_mcap",
        # 稳定币市值无固定"顶部阈值"，用同比变化代理；这里简化为占位，建议手工微调
        "progress": ("progress_up", 0.0, 1.0),
        "enabled": False,   # 需要历史基线，路线A先关，避免假信号
    },
    {
        "key": "funding",
        "notion_name": "Funding Rate",
        "source": "bg_funding_rate",
        "progress": ("progress_up", 0.0, 0.1),     # 当前费率%，>=0.1%过热
        "enabled": True,
    },
    {
        "key": "coinbase_premium",
        "notion_name": "Coinbase Premium",
        "source": "coinbase_premium",
        # 溢价是双向情绪，归一化较弱；先关，避免误导
        "progress": ("progress_up", 0.0, 0.5),
        "enabled": False,
    },
    {
        "key": "etf_flow",
        "notion_name": "ETF 净流",
        "source": None,        # SoSoValue 无稳定公开API，路线A手填
        "progress": ("progress_up", 0.0, 1.0),
        "enabled": False,
    },
]

# 进度函数名 -> 实际函数 的解析表
PROGRESS_FUNCS = {
    "progress_up": progress_up,
    "progress_down": progress_down,
}
