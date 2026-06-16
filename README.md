# BTC 三层共振信号库 — 自动更新

每天定时从免费数据源采集 BTC 周期指标，自动写回 Notion 数据库。
零本地占用（跑在 GitHub 云端）、零成本（公开仓库免费）、零开机要求。

---

## 它做什么

- 每天美国东部时间 15:00（夏令时 = UTC 19:00）自动运行一次
- 采集启用的指标当前值 → 计算「距顶进度 0-100」→ 写回 Notion
- Notion 里的「档位得分」「加权得分」是公式列，会自动重算总分
- 任何单个数据源失败都只跳过该指标，不影响其余

## 当前自动化覆盖（10/13）

| 状态 | 指标 | 数据源 |
|------|------|--------|
| 自动 | Mayer Multiple | CoinGecko 自算 |
| 自动 | Pi Cycle Top | CoinGecko 自算 |
| 自动 | 200周均线偏离 | CoinGecko 自算 |
| 自动 | MVRV Z-score | BGeometrics 直出 |
| 自动 | NUPL | BGeometrics 直出 |
| 自动 | SOPR | BGeometrics 直出 |
| 自动 | Puell Multiple | BGeometrics 直出 |
| 自动 | 联邦基金利率 FFR | FRED |
| 自动 | 恐惧贪婪指数 | BGeometrics 直出 |
| 自动 | Funding Rate | BGeometrics 直出 |
| 手填 | ETF 净流 | 口径需确认，暂关 |
| 手填 | 稳定币供应 | 需历史基线，暂关 |
| 手填 | Coinbase Premium | 双向情绪难归一化，暂关 |

链上估值层四核心（MVRV/NUPL/SOPR/Puell）已通过 BGeometrics 免费 API 实现自动化。
剩余 3 条因数据口径问题暂留手填，详见末尾「升级路线」。

---

## 部署步骤（全程在浏览器完成，不碰本地硬盘）

### 第 1 步：拿三把钥匙

**① Notion 集成 token**
1. 打开 https://www.notion.so/my-integrations → New integration（已建过则跳过）
2. 名称随意，关联到你的 workspace
3. 复制 `Internal Integration Secret`（形如 `secret_xxx` / `ntn_xxx`）

**给集成授权访问目标页面**（关键，否则脚本写不进）：
方式一（推荐，实测有效）：
- 打开 https://app.notion.com/developers/connections
- 找到你的集成（如 `crypto-signals`）→ 选 `Content access` 选项卡 → 点 `Edit access`
- 搜索并勾选「加密市场信号判断仪表盘」页面 → 保存
- 注意：列表里可能出现 **Shared** 和 **Private** 两栏同名页面（分别代表该页在团队空间 / 私人空间的归属）。**两个都勾选最保险**，避免漏授权脚本实际写入的那一个。

方式二（备选）：在页面右上 `•••` → Connections → 添加集成。
（部分 Notion 版本菜单位置不同，找不到就用方式一）

**② FRED 免费 key**
1. 打开 https://fred.stlouisfed.org/docs/api/api_key.html
2. 注册（免费）→ 申请 key → 复制

**③ BGeometrics token**
1. 登录 https://bitcoin-data.com/bguser/login
2. 进入 profile 页（https://bitcoin-data.com/bguser/profile）→ 复制 API token
3. 免费档限额 8次/小时、15次/天（每天跑一次绰绰有余）

### 第 2 步：建公开仓库

1. https://github.com/new → 仓库名如 `crypto-signals`
2. 选 **Public**（公开 = Actions 永久免费，无 2000 分钟限制）
3. 创建后，把本项目所有文件传上去（网页端 `Add file → Upload files` 即可，
   或用 git push）。文件清单：
   ```
   config.py
   fetch_signals.py
   main.py
   requirements.txt
   .gitignore
   .github/workflows/daily.yml
   README.md
   ```

> ⚠️ 安全：代码里**没有任何密钥**，token 全部走下一步的 Secrets。
> 公开仓库谁都能看代码，但看不到 Secrets。

### 第 3 步：填 Secrets

仓库页 → Settings → Secrets and variables → Actions → New repository secret，
建两条：

| Name | Value |
|------|-------|
| `NOTION_TOKEN` | 第1步的 `secret_xxx` |
| `FRED_API_KEY` | 第1步的 FRED key |
| `BGAPI_TOKEN` | BGeometrics token（profile 页获取，补链上指标用） |

### 第 4 步：首次手动验证

1. 仓库页 → Actions 标签 → 左侧选 `Daily BTC Signals Update`
2. 右侧 `Run workflow` 按钮 → 点绿色 Run
3. 等 1-2 分钟，点进运行记录看日志
4. 成功标志：日志出现 `[ok] Mayer Multiple: ...` 等，结尾 `成功 N | 失败 M`
5. 去 Notion 看：对应指标的「当前值」「当前进度」「最近更新」已刷新

之后每天美东 15:00（夏令时 = UTC 19:00）自动跑，无需任何操作。

---

## 调整与维护

- **改阈值 / 开关指标**：只改 `config.py` 的 `INDICATORS`，提交即生效
- **改运行时间**：改 `.github/workflows/daily.yml` 的 cron
- **看历史运行**：仓库 Actions 标签，每次跑都有完整日志
- **某指标常失败**：多半是该源地域限制或改版，看日志 `[warn]` 定位

## 防超额（即使改私有仓库也安全）

GitHub 默认支出上限 $0，私有仓库超免费额度会**直接停跑而非扣费**。
本任务每月用量 < 60 分钟，公开仓库下完全免费。

---

## 升级路线（可选，日后再说）

- **补全手填 3 项**：ETF 净流 / 稳定币 / Coinbase Premium，需确认各自的「距顶进度」归一化口径后接入（BGeometrics 多数已有端点）
- **交叉验证**：链上四核心可同时拉 Coinmetrics 自算值与 BGeometrics 直出值对比，提升可信度
- **路线 C（如需绝对权威）**：接 Glassnode 付费 API（约 $800/月），数据源最权威，但对个人偏重

两者都只需在 `fetch_signals.py` 加采集函数、`config.py` 把对应指标 `enabled` 改 True。
