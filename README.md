# Triple Screen Scanner

基于 Alexander Elder 三重过滤系统的多时间框架信号扫描器，面向美股 Top 300 批量资产、小时级扫描和 Telegram 实时推送。

当前结构重点是保持职责分离，同时避免同名目录嵌套：

- 业务参数集中放在 `config/settings.yaml`
- 密钥只放在 `.env`，不再写进 Python 文件
- 策略、数据源、通知、存储、入口模块并列放在同一层
- 保留 `python src/scanner.py` 这种简单启动方式
- 新增 `python src/universe_optimizer.py` 用于把候选股票池从 300 压缩到更聚焦的 100

## 目录结构

```text
triple_screen/
├── config/
│   └── settings.yaml              # 主配置文件（非敏感）
├── data/                          # SQLite 数据目录（运行时生成）
├── logs/                          # 日志目录（运行时生成）
├── requirements.txt
├── src/
│   ├── scanner.py                 # CLI 入口
│   ├── runner.py                  # 调度入口
│   ├── scan_engine.py             # 扫描编排
│   ├── loader.py                  # YAML + .env 配置加载
│   ├── schema.py                  # 配置数据结构
│   ├── alpaca.py                  # Alpaca 数据接入
│   ├── telegram.py                # Telegram 推送
│   ├── sqlite.py                  # SQLite 存储
│   └── indicators.py              # 三重过滤指标与评分
└── .env.example                   # 密钥示例
```

## 配置方式

业务配置统一放在 [config/settings.yaml](/Users/jay/workspace/my_github/triple_screen/config/settings.yaml)，股票池单独放在 [config/universe_us_top300.yaml](/Users/jay/workspace/my_github/triple_screen/config/universe_us_top300.yaml)。

敏感信息放在 `.env`，例如：

```env
ALPACA_API_KEY_ID=your_alpaca_api_key_id
ALPACA_API_SECRET_KEY=your_alpaca_api_secret_key
ALPACA_MARKET_DATA_BASE_URL=https://data.alpaca.markets/v2
ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets/v2
ALPHAVANTAGE_API_KEY=your_alpha_vantage_api_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

`.env` 已被 `.gitignore` 忽略，因此不会被提交到仓库。

## 快速开始

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 配置密钥

```bash
cp .env.example .env
```

3. 修改 [config/settings.yaml](/Users/jay/workspace/my_github/triple_screen/config/settings.yaml)

你可以在这里集中调整：

- 扫描股票池规模
- MACD / RSI / Breakout 参数
- 候选池 RR / 财报黑窗 / 强背离阈值
- 候选池展示数量与 Triggered 推送数量
- 并发数、扫描频率、日志路径
- SQLite 路径
- Telegram 是否启用

如果你想手动增删股票，直接修改 [config/universe_us_top300.yaml](/Users/jay/workspace/my_github/triple_screen/config/universe_us_top300.yaml) 里的 `symbols` 列表即可。

## 运行特性

- 本地 SQLite 会缓存周线、日线、1 小时 K 线
- 后续扫描默认走增量更新，而不是每次都全量重拉
- Alpaca 股票历史数据默认走 `feed: iex`，适合免费账户直接使用
- base URL 现在支持两种写法：`https://.../v2` 或不带 `/v2` 的根域名，代码会自动规整
- 小时线增量刷新已改成精确时间窗口，不再只按日期拉取
- 扫描开始时会先对 YAML 股票池做批量请求预热缓存，再在本地逐票计算指标，避免 300 只股票逐票逐周期打 API

## 股票池优化器

项目现在提供一个独立的股票池优化模块 [src/universe_optimizer.py](/Users/jay/workspace/my_github/triple_screen/src/universe_optimizer.py)，用于把较大的股票池压缩成更聚焦的候选池。

当前实现优先使用你已经有的 Alpaca 日线行情，围绕这些维度打分：

- 流动性：20 日平均成交额
- 波动机会：ATR 占股价比例是否落在合适区间
- 风险调整后动量：6 个月 / 12 个月动量，排除最近 1 个月
- 相对强弱：相对 `SPY`、`QQQ`
- 趋势质量：`close`、`EMA50`、`EMA200` 的位置关系
- 可选扩展字段：`roe_ttm`、`debt_to_equity`、`accruals_ratio`、`earnings_revision_1m`、`short_interest_pct_float`、`days_to_cover`

默认会输出：

- 直接按综合机会分数排序后的前 `100` 只
- `LONG / SHORT` 比例不做强制约束
- 如果你想人为约束方向数量，可以额外传 `--long-count` 或 `--short-count`

直接用当前配置里的股票池：

```bash
python src/universe_optimizer.py --top-k 100 --output-file config/universe_us_top100_optimized.yaml
```

指定本地股票列表文件：

```bash
python src/universe_optimizer.py \
  --input-file config/universe_us_top300.yaml \
  --top-k 100 \
  --output-file config/universe_us_top100_optimized.yaml
```

从远程 YAML / JSON / CSV 地址直接拉一份列表再筛：

```bash
python src/universe_optimizer.py \
  --input-url https://example.com/my_universe.yaml \
  --input-format auto \
  --output-file config/universe_us_top100_optimized.yaml
```

输出文件仍然是 `symbols:` 结构，所以你后续如果想直接拿去给扫描器用，只需要把 [config/settings.yaml](/Users/jay/workspace/my_github/triple_screen/config/settings.yaml) 里的 `universe.static_file` 指向新文件即可。

## Alpaca 配置说明

- 股票历史数据域名默认使用 `https://data.alpaca.markets/v2`
- Trading API 默认使用 `https://paper-api.alpaca.markets/v2`
- 资产列表（用于非 `static_file` 股票池）会自动请求 `assets`，无需你手动关心 `/v2` 是否重复
- 认证使用请求头 `APCA-API-KEY-ID` 与 `APCA-API-SECRET-KEY`
- `static_file` / `custom` 股票池模式可直接使用；若使用原来的动态 Top N 模式，当前会退化为从 Alpaca active assets 中筛选前 N 个，因为 Alpaca 不提供 Polygon 那种市值排序接口
- 默认 `feed: iex`
  IEX 是 Alpaca 文档里说明的免费可用股票 feed；如果你有 SIP 订阅，可把 [config/settings.yaml](/Users/jay/workspace/my_github/triple_screen/config/settings.yaml) 里的 `feed` 改成 `sip`
- 默认 `adjustment: split`
  这更接近原来 Polygon `adjusted=true` 的拆股调整语义
- 默认主动限速为 `180 req/min`
  Alpaca 文档中的 Trading API Basic 计划历史数据上限是 `200 / min`，这里预留了缓冲，避免在分页和重试时贴线
- 当前批量策略会对 `config/universe_us_top300.yaml` 中的 300 只股票按 timeframe 分别请求批量 bars 接口
  常规扫描会收敛到少量批量请求加分页，而不是原先的几百次单票请求
- 如果你使用 paper 账户，可直接在 `.env` 里设置 `ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets/v2`

## 止损模型

- 盘中信号的 `初始止损` 会锚定在小时信号K或日线回调摆点，作为入场时真实风险基准
- 初始止损建议目前只保留两种：`SafeZone` 与 `尼克止损法`
  默认 `SafeZone` 使用 `EMA22` 统计最近 `10` 根K线对趋势线的穿透噪音；做多默认乘数 `2.0`，做空默认乘数 `3.0`
- 持仓后的 `保护止损` 目前改为按最新日线柱极值外侧的 `ATR` 移动止损
  会同时给出 `1x ATR` 与 `2x ATR` 两档位置；系统默认把 `1x ATR` 作为当前建议移动止损
- 收盘后更新未平仓交易时，只会用 ATR 移动止损单向推进保护止损，不会回头改写初始止损
- Journal 中 `stop_loss` 表示当前生效的活动止损；`initial_stop_loss` 会保留开仓时的初始防守位

4. 运行一次扫描

收盘后更新候选池：

```bash
python src/scanner.py --once --mode eod
```

次日盘中按上一交易日候选池扫描触发：

```bash
python src/scanner.py --once --mode intraday
```

自动按市场时段选择模式：

```bash
python src/scanner.py --once
```

如果你想验证整条链路但不发送 Telegram：

```bash
python src/scanner.py --once --dry-run
```

5. 持续运行

```bash
python src/scanner.py --loop
```

## 交易日志 Web UI

项目现在内置了一个基于 FastAPI + SQLite 的交易日志前端，页面文件在 [index.html](/Users/jay/workspace/my_github/triple_screen/frontend/trade_journal/index.html)。

启动本地 Journal Server：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export JOURNAL_AUTH_USERNAME=your_username
export JOURNAL_AUTH_PASSWORD=your_password
PYTHONPATH=src .venv/bin/python -m journal
```

启动后访问：

- [http://127.0.0.1:8100/](http://127.0.0.1:8100/)
- [http://127.0.0.1:8100/api/health](http://127.0.0.1:8100/api/health)

如果部署到 AWS 并想直接用实例公网 IP 访问，不需要 nginx，也不需要域名：

- `http://<EC2_PUBLIC_IP>:8100/`
- `http://<EC2_PUBLIC_IP>:8100/api/health`

这种方式下前端和 API 都是同一个 FastAPI 进程提供的，所以不需要额外配置 CORS。

如果设置了 `JOURNAL_AUTH_USERNAME` 和 `JOURNAL_AUTH_PASSWORD`，浏览器打开页面时会直接弹出最简单的用户名/密码框。对个人使用场景，这已经比裸露公网安全很多。

当前 Journal Server 提供：

- 交易记录 CRUD，统一写入本地 SQLite
- 风险设置持久化到 `trade_settings`
- 页面不再直连 Supabase，而是通过本地 `/api` 访问

另外，收盘后 `--mode eod` 现在会在更新候选池后，顺带读取所有未平仓交易并更新保护性止损：

- 多头止损只会上调，不会下调
- 空头止损只会下移，不会上移
- 如果仍持有未来几天内将发布财报的股票，会在 Telegram 汇总里追加提醒，提示提前卖出或减仓
- 更新结果会和候选池摘要一起汇总到 Telegram

## 调度建议

更推荐用 cron 或 systemd 以 one-shot 方式每小时调用一次：

```bash
0 * * * * cd /path/to/triple_screen && /usr/bin/python3 src/scanner.py --once >> logs/cron.log 2>&1
```

这样比在 Python 里常驻 `while True` 更易维护，也更符合生产环境习惯。

AWS EC2 + systemd 的部署模板已经放在 [deploy/aws/README.md](/Users/jay/workspace/my_github/triple_screen/deploy/aws/README.md) 和 [triple-screen.service](/Users/jay/workspace/my_github/triple_screen/deploy/aws/systemd/triple-screen.service)、[triple-screen.timer](/Users/jay/workspace/my_github/triple_screen/deploy/aws/systemd/triple-screen.timer)。

当前 timer 已按美东时间配置为：

- 周一到周五盘中每小时一次：`09:30` 到 `15:30`
- 每个交易日收盘后一次：`16:10`

推荐做法是：

- `16:10` 跑 `--mode eod`，更新当天候选池
- 次日盘中每小时跑 `--mode intraday`，只扫描上一已收盘交易日候选池里的股票

## 当前实现说明

- 已完成：集中配置、密钥隔离、包结构重组、扫描编排分层、Telegram/SQLite/Alpaca 模块化
- 已完成：收盘后构建候选池，次日盘中严格只扫描上一交易日候选池，不再在缺少候选池时自动回退为全市场重建
- 仍可继续增强：原始 K 线增量缓存、失败重试队列、指标快照版本化、更多通知渠道
