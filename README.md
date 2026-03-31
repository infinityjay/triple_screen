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
│       │   │   └── polygon.py     # Polygon 数据接入
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
POLYGON_API_KEY=your_polygon_api_key
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
- Polygon 请求带主动速率限制，优先避免触发套餐限流

4. 运行一次扫描

```bash
python src/scanner.py --once
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

## 当前实现说明

- 已完成：集中配置、密钥隔离、包结构重组、扫描编排分层、Telegram/SQLite/Polygon 模块化
- 仍可继续增强：原始 K 线增量缓存、失败重试队列、指标快照版本化、更多通知渠道
