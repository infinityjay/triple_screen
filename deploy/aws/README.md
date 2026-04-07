# AWS Deployment

当前项目可以直接部署到单台 EC2，并通过 `systemd timer` 在美股交易时段定时扫描。

## 推荐环境

- AMI: Amazon Linux 2023
- 机型: `t3.small` 或 `t3.medium`
- 磁盘: `gp3` 20GB+

## 当前项目结构

部署时不再使用 `src/triple_screen/...` 这种嵌套包结构，当前入口和核心模块都是扁平放在 `src/` 下：

```text
src/
├── scanner.py       # CLI 入口
├── runner.py        # 调度入口
├── scan_engine.py   # 扫描主流程
├── indicators.py    # 三重过滤指标逻辑
├── alpaca.py        # Alpaca 数据访问
├── telegram.py      # Telegram 推送
├── sqlite.py        # SQLite 存储
├── loader.py        # 配置加载
└── schema.py        # 配置结构
```

生产环境的启动命令就是：

```bash
python src/scanner.py --once
```

## 当前扫描逻辑

当前实现遵循《以交易为生》的三重过滤主线，但输出方式做了更适合盘中扫描的调整：

- 周线负责判断大方向，并以确认 bars 作为硬过滤，只保留真正可行的趋势机会
- 日线负责寻找与周线方向一致的回调机会
- 收盘后会把所有通过过滤的标的写入候选池，默认消息展示前 15 个
- 小时线盘中只扫描上一已收盘交易日的候选池，判断是否已经触发突破/跌破
- 扫描结束后会推送按交易价值排序的 Top Qualified 列表，默认展示 15 个
- 如果某个标的已经触发，会按 Triggered 排名单独发送机会消息，默认最多 3 个
- 即使本次没有触发型信号，也会发送扫描汇总，列出已通过过滤的观察机会

换句话说，当前不会像旧逻辑那样因为小时线尚未突破就直接把前两层已经成立的 setup 丢掉。

## 服务器初始化

```bash
sudo dnf update -y
sudo dnf install -y git python3 python3-pip
cd /home/ec2-user
git clone <your-repo-url> triple_screen
cd triple_screen
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p data logs
```

把真实凭证写入：

`/home/ec2-user/triple_screen/.env`

至少需要这些变量：

```env
ALPACA_API_KEY_ID=...
ALPACA_API_SECRET_KEY=...
ALPACA_MARKET_DATA_BASE_URL=https://data.alpaca.markets/v2
ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets/v2
ALPHAVANTAGE_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## 上线前检查

先确认配置和依赖都正常：

```bash
cd /home/ec2-user/triple_screen
source .venv/bin/activate
python src/scanner.py --help
```

## Dry Run

启用定时器前，先做一次 dry-run：

```bash
cd /home/ec2-user/triple_screen
source .venv/bin/activate
python src/scanner.py --once --dry-run
```

`--dry-run` 的行为是：

- 会真实访问 Alpaca
- 会更新本地缓存和 SQLite
- 不会发送 Telegram
- 不会更新 alert cooldown 记录

你应该在日志里看到：

- 股票池加载成功
- 候选池或盘中触发扫描完成
- 周线 / 日线 / 小时线批量请求成功
- `market trend: ...`
- `QUALIFIED TOP ...` 或 `TRIGGERED TOP ...`

## 正式单次扫描

在启用 timer 之前，建议再做一次真实扫描：

```bash
cd /home/ec2-user/triple_screen
source .venv/bin/activate
python src/scanner.py --once
```

预期行为：

- Telegram 会收到“开始扫描”消息
- 如果有已触发机会，会逐条收到机会消息
- 每轮会先收到 Top Qualified 摘要，再收到 Top Triggered 的详细消息

如果你想分开调度，也可以显式指定：

```bash
python src/scanner.py --once --mode eod
python src/scanner.py --once --mode intraday
```

## 安装 systemd

复制模板文件：

```bash
sudo cp deploy/aws/systemd/triple-screen.service /etc/systemd/system/
sudo cp deploy/aws/systemd/triple-screen.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now triple-screen.timer
```

当前 service 的执行命令是：

```ini
ExecStart=/home/ec2-user/triple_screen/.venv/bin/python src/scanner.py --once
```

这与当前项目结构一致，不需要再改成包形式入口。

## 定时规则

当前 timer 使用 `America/New_York` 时区，覆盖美股常规交易时段：

- 周一到周五 `09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30`
- 周一到周五 `16:10` 再补一次收盘后扫描

因为 `OnCalendar=` 明确带了 `America/New_York`，夏令时由 systemd 自动处理。

这套配置对应的实际行为是：

- `16:10` 执行一次收盘候选池构建
- 下一交易日 `09:30` 到 `15:30` 每小时只扫描上一交易日候选池
- 如果上一交易日候选池缺失，盘中扫描会直接退出并记录告警，不会自动回退成全市场重建

## 验证部署

```bash
systemctl list-timers | grep triple-screen
systemctl status triple-screen.timer
systemctl status triple-screen.service
journalctl -u triple-screen.service -n 100 --no-pager
tail -f /home/ec2-user/triple_screen/logs/systemd.log
```

如果要手动触发一轮：

```bash
sudo systemctl start triple-screen.service
```

## 常见检查点

- `.env` 必须放在仓库根目录，不是 `src/` 目录
- `config/settings.yaml` 默认会从项目根目录读取
- `data/` 和 `logs/` 目录需要可写
- 首次运行时会批量回补 K 线缓存，耗时会比后续增量扫描更长
- 如果 Telegram 没消息，先看 `journalctl` 和 `logs/systemd.log` 是否有 `Telegram send failed`
- 如果 Alpaca 拉数失败，优先检查安全组、实例出网和 DNS

## 备注

- 本模板假设仓库路径是 `/home/ec2-user/triple_screen`
- 如果你的实际路径不同，需要同步修改 `User`、`WorkingDirectory` 和 `ExecStart`
- `.env` 只保留在服务器上，不要提交到仓库
