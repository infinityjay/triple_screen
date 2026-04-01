# Triple Screen Scanner

基于 Alexander Elder 三重过滤系统的多时间框架信号扫描器，面向美股 Top 300 批量资产、小时级扫描和 Telegram 实时推送。

这次重构后的重点不是把逻辑继续堆在单个 `scanner.py` 里，而是把项目整理成适合长期维护的工程化结构：

- 业务参数集中放在 `config/settings.yaml`
- 密钥只放在 `.env`，不再写进 Python 文件
- 策略、数据源、通知、存储、应用入口分层
- 保留 `python src/scanner.py` 这种简单启动方式

## 目录结构

```text
triple_screen/
├── config/
│   └── settings.yaml              # 主配置文件（非敏感）
├── data/                          # SQLite 数据目录（运行时生成）
├── logs/                          # 日志目录（运行时生成）
├── requirements.txt
├── src/
│   ├── scanner.py                 # 兼容入口
│   └── triple_screen/
│       ├── __init__.py
│       ├── __main__.py
│       ├── runner.py              # CLI / 调度入口
│       ├── application/
│       │   └── scanner.py         # 扫描编排
│       ├── config/
│       │   ├── loader.py          # YAML + .env 配置加载
│       │   └── schema.py          # 配置数据结构
│       ├── infrastructure/
│       │   ├── data/
│       │   │   └── alpaca.py      # Alpaca 数据接入
│       │   ├── notifications/
│       │   │   └── telegram.py    # Telegram 推送
│       │   └── storage/
│       │       └── sqlite.py      # SQLite 存储
│       └── strategy/
│           └── indicators.py      # 三重过滤指标与评分
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
- 风控参数
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

4. 运行一次扫描

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

## 调度建议

更推荐用 cron 或 systemd 以 one-shot 方式每小时调用一次：

```bash
0 * * * * cd /path/to/triple_screen && /usr/bin/python3 src/scanner.py --once >> logs/cron.log 2>&1
```

这样比在 Python 里常驻 `while True` 更易维护，也更符合生产环境习惯。

AWS EC2 + systemd 的部署模板已经放在 [deploy/aws/README.md](/Users/jay/workspace/my_github/triple_screen/deploy/aws/README.md) 和 [triple-screen.service](/Users/jay/workspace/my_github/triple_screen/deploy/aws/systemd/triple-screen.service)、[triple-screen.timer](/Users/jay/workspace/my_github/triple_screen/deploy/aws/systemd/triple-screen.timer)。

## 当前实现说明

- 已完成：集中配置、密钥隔离、包结构重组、扫描编排分层、Telegram/SQLite/Alpaca 模块化
- 仍可继续增强：原始 K 线增量缓存、失败重试队列、指标快照版本化、更多通知渠道
