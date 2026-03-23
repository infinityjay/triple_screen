# 三重过滤交易系统

基于 Alexander Elder《以交易为生》三重过滤（Triple Screen）系统，监测美股市场并每日生成信号报表，通过 GitHub Pages 发布。

## 功能

- 每个交易日收盘后自动拉取数据、计算指标、生成 HTML 报表
- 报表自动 push 到 GitHub，通过 GitHub Pages 在浏览器访问
- Watchlist 在 `config.yaml` 中配置，修改后下次运行立即生效

## 三重过滤逻辑

| 过滤层 | 数据 | 指标 | 作用 |
|--------|------|------|------|
| 第一重 | 周线 | EMA13 + MACD histogram | 判断大趋势方向 |
| 第二重 | 日线 | Stochastic K/D | 找逆势回调入场点 |
| 第三重 | 日线 | 价格相对 EMA22 | 确认入场位置 |

**信号类型：**
- `买入`：三重共振，趋势上行 + KD超卖后金叉
- `卖出`：三重共振，趋势下行 + KD超买后死叉
- `关注做多`：趋势向上，等待回调
- `关注做空`：趋势向下，等待反弹
- `中性`：条件不满足

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 初始化历史数据（首次运行）

```bash
python src/daily_run.py --init
```

首次下载约需 2–5 分钟（10年历史数据）。

### 3. 手动运行一次测试

```bash
# 运行但不推送 GitHub（测试用）
python src/daily_run.py --no-push

# 运行并推送
python src/daily_run.py
```

### 4. 设置 cron 定时任务（云服务器）

```bash
crontab -e
```

添加（每周一至周五 UTC 21:30，即美东时间 16:30）：

```
30 21 * * 1-5 cd /path/to/triple-screen && /usr/bin/python3 src/daily_run.py >> logs/cron.log 2>&1
```

## 开启 GitHub Pages

1. GitHub 仓库 → **Settings** → **Pages**
2. Source 选择 `Deploy from a branch`
3. Branch: `main`，文件夹选 `/docs`
4. 保存后访问：`https://你的用户名.github.io/triple-screen/`

## 配置 Watchlist

编辑 `config.yaml`，添加或删除股票：

```yaml
watchlist:
  - ticker: AAPL
    name: "Apple"
    group: "科技股"
  - ticker: SPY
    name: "S&P 500 ETF"
    group: "大盘指数"
```

修改后无需重启，下次 cron 执行时自动读取新配置。新增的 ticker 会自动补充10年历史数据。

## 项目结构

```
triple-screen/
├── config.yaml          # Watchlist 和参数配置
├── requirements.txt
├── src/
│   ├── config_loader.py   # 读取 config.yaml
│   ├── data_fetcher.py    # yfinance 数据获取
│   ├── storage.py         # SQLite 本地存储
│   ├── indicators.py      # 三重过滤指标计算
│   ├── report_generator.py# HTML 报表生成
│   ├── git_push.py        # 自动推送 GitHub
│   └── daily_run.py       # 主入口（cron 调用此文件）
├── data/
│   └── market.db          # 本地数据库（不进 git）
├── docs/                  # GitHub Pages 根目录
│   ├── index.html         # 历史报表导航首页
│   └── reports/           # 每日报表
│       └── YYYY-MM-DD.html
└── logs/
    └── run.log            # 运行日志（不进 git）
```

## 数据源

使用 [yfinance](https://github.com/ranaroussi/yfinance)，免费，无需注册 API Key，数据来自 Yahoo Finance。
