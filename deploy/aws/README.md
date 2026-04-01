# AWS Deployment

This project is ready to run on a single EC2 instance with `systemd`.

## Recommended Target

- AMI: Amazon Linux 2023
- Instance: `t3.small` or `t3.medium`
- Disk: `gp3` 20GB+

## Server Setup

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

Create `/home/ec2-user/triple_screen/.env` with your real credentials.

## Dry Run

Use this before enabling the hourly timer:

```bash
cd /home/ec2-user/triple_screen
source .venv/bin/activate
python src/scanner.py --once --dry-run
```

`--dry-run` keeps cache/database updates but suppresses Telegram sends and alert-log updates.

## Install systemd Units

Copy the provided files:

```bash
sudo cp deploy/aws/systemd/triple-screen.service /etc/systemd/system/
sudo cp deploy/aws/systemd/triple-screen.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now triple-screen.timer
```

## Verify

```bash
systemctl list-timers | grep triple-screen
journalctl -u triple-screen.service -n 100 --no-pager
tail -f /home/ec2-user/triple_screen/logs/systemd.log
```

## Manual Run

```bash
sudo systemctl start triple-screen.service
```

## Notes

- The service file assumes the repo lives at `/home/ec2-user/triple_screen`
- Adjust `User`, `WorkingDirectory`, and `ExecStart` if your paths differ
- Keep `.env` on the server only; do not commit it
