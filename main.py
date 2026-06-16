"""
BTC 三层共振信号库 - 主程序
================================
流程：
  1. 从 Notion 数据库查询所有行，建立 指标名 -> page_id 映射
  2. 遍历 config.INDICATORS 中 enabled=True 的指标
  3. 调用对应采集函数取当前值
  4. 用 progress 规则算「当前进度」(0-100)
  5. 写回 Notion 的「当前值」「当前进度」「最近更新」字段
     （档位得分/加权得分是公式列，Notion 自动重算）

环境变量（GitHub Secrets）：
  NOTION_TOKEN   - Notion 集成 token（secret_xxx）
  FRED_API_KEY   - FRED 免费 key

本地测试：
  export NOTION_TOKEN=... FRED_API_KEY=...
  python3 main.py
"""

import os
import sys
import datetime
import requests

import config
from fetch_signals import FETCHERS

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_API = "https://api.notion.com/v1"


def _notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": config.NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _paginate_query(url, api_version=None):
    """
    对给定 query 端点分页抓取所有行，返回 {指标标题: page_id}。
    成功返回 (mapping, None)；HTTP 错误返回 (部分mapping, 错误信息)。
    api_version 可覆盖默认版本号（data source 端点需较新版本）。
    """
    headers = _notion_headers()
    if api_version:
        headers["Notion-Version"] = api_version
    mapping = {}
    payload = {"page_size": 100}
    while True:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            return mapping, f"HTTP {r.status_code}: {r.text[:300]}"
        data = r.json()
        for row in data.get("results", []):
            props = row.get("properties", {})
            title_prop = props.get("指标", {}).get("title", [])
            if title_prop:
                name = "".join(t.get("plain_text", "") for t in title_prop).strip()
                if name:
                    mapping[name] = row["id"]
        if data.get("has_more"):
            payload["start_cursor"] = data["next_cursor"]
        else:
            break
    return mapping, None


def query_rows():
    """
    查询数据库所有行，返回 {指标标题: page_id}。

    Notion 在 2025-09 把 database 拆成了 data source 层：
    - 旧端点 /v1/databases/{id}/query 在新版 API 上可能失效/返回空
    - 新端点 /v1/data_sources/{id}/query 是 data source 维度

    采用三级回退，保证不同 API 版本都能查到行：
      ① 标准 databases/{id}/query（旧版，多数情况可用）
      ② data_sources/{id}/query（新版 API）
      ③ 都失败则打印明确报错，便于定位
    """
    # ① 标准 database 端点
    url1 = f"{NOTION_API}/databases/{config.NOTION_DATABASE_ID}/query"
    mapping, err = _paginate_query(url1)
    if mapping:
        return mapping
    if err:
        print(f"[warn] databases/query 失败({err})，回退 data_sources 端点 ...")
    else:
        print("[warn] databases/query 返回 0 行，回退 data_sources 端点 ...")

    # ② data source 端点（新版，需 2025-09-03 版本）
    url2 = f"{NOTION_API}/data_sources/{config.NOTION_DATA_SOURCE_ID}/query"
    mapping2, err2 = _paginate_query(url2, api_version="2025-09-03")
    if mapping2:
        return mapping2

    # ③ 都失败
    if err2:
        print(f"[error] data_sources/query 也失败: {err2}")
    print("[error] 两种端点均未查到行。排查：")
    print("  1) 集成是否已在 Content access 授权该页面（含数据库的那一页）")
    print("  2) NOTION_DATABASE_ID / NOTION_DATA_SOURCE_ID 是否正确")
    print("  3) 标题列名是否仍为「指标」")
    return mapping2 or mapping


def update_row(page_id, value, progress):
    """写回单行的 当前值 / 当前进度 / 最近更新"""
    today = datetime.date.today().isoformat()
    props = {
        "当前值": {"rich_text": [{"text": {"content": _fmt_value(value)}}]},
        "最近更新": {"date": {"start": today}},
    }
    if progress is not None:
        props["当前进度"] = {"number": round(progress, 2)}
    r = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=_notion_headers(),
        json={"properties": props},
        timeout=20,
    )
    return r.status_code == 200, (r.text[:200] if r.status_code != 200 else "")


def _fmt_value(v):
    """数值格式化为可读文本"""
    if v is None:
        return ""
    if abs(v) >= 1e9:
        return f"{v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{v/1e6:.2f}M"
    if abs(v) < 1 and v != 0:
        return f"{v:.4f}"
    return f"{v:.2f}"


def compute_progress(ind, value):
    """按 config 规则计算进度 0-100"""
    fn_name, *args = ind["progress"]
    fn = config.PROGRESS_FUNCS[fn_name]
    return fn(value, *args)


def main():
    if not NOTION_TOKEN:
        print("[fatal] NOTION_TOKEN 未设置")
        sys.exit(1)

    print("=" * 50)
    print(f"BTC 信号自动更新  {datetime.datetime.utcnow().isoformat()}Z")
    print("=" * 50)

    print("\n[1/3] 查询 Notion 行映射 ...")
    rows = query_rows()
    print(f"  找到 {len(rows)} 行: {list(rows.keys())}")
    if not rows:
        print("[fatal] 未查到任何行，检查 token 权限或数据库ID")
        sys.exit(1)

    print("\n[2/3] 采集 + 计算 + 写回 ...")
    ok, fail, skip = 0, 0, 0
    for ind in config.INDICATORS:
        name = ind["notion_name"]
        if not ind.get("enabled"):
            print(f"  [skip] {name}（手填）")
            skip += 1
            continue
        if name not in rows:
            print(f"  [skip] {name}（Notion 中无此行）")
            skip += 1
            continue

        fetcher = FETCHERS.get(ind["source"])
        if fetcher is None:
            print(f"  [skip] {name}（无采集器）")
            skip += 1
            continue

        print(f"  采集 {name} ...")
        value = fetcher()
        if value is None:
            print(f"  [fail] {name} 采集失败，跳过")
            fail += 1
            continue

        progress = compute_progress(ind, value)
        success, err = update_row(rows[name], value, progress)
        if success:
            prog_str = f"{progress:.1f}%" if progress is not None else "—"
            print(f"  [ok]   {name}: 值={_fmt_value(value)} 进度={prog_str}")
            ok += 1
        else:
            print(f"  [fail] {name} 写回失败: {err}")
            fail += 1

    print("\n[3/3] 完成")
    print(f"  成功 {ok} | 失败 {fail} | 跳过 {skip}")
    # 失败不让整个 Action 标红（部分源地域受限是预期内的），仅记录
    print("=" * 50)


if __name__ == "__main__":
    main()
