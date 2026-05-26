# Deploy: systemd units

Process management lives in systemd, not in `start.sh`. The script only sends
restart commands — single-instance, auto-restart, and boot autostart are all
the kernel/systemd's job. This doc captures the unit definitions so they can
be reinstalled on a fresh box.

## Why systemd

Bash kill-and-launch scripts have race windows: between `pgrep` and `kill`,
between `kill` and verifying it died, between launch and the post-launch
duplicate check. The V1 dual-instance incident (2026-03-24, real money lost
to doubled positions) happened despite a launcher with all those checks.

systemd doesn't race because it owns its own cgroup-tracked children. There
is no "between" — `systemctl restart` is one transition, atomic. Two operators
typing `systemctl start` at the same time is also atomic (the second is a
no-op because the unit is already active). No PID files, no pgrep patterns,
no TOCTOU on lockfiles.

It also gets us crash-restart and reboot-autostart for free, which the old
bash script didn't have at all.

## Units

Two service files live in `/etc/systemd/system/` on this host:

- **`kalshibot3.service`** — FastAPI backend (uvicorn on 127.0.0.1:8000)
- **`kalshibot3-dashboard.service`** — `vite build --watch` rebuilding
  `dashboard/dist/` on every source save

Both files are checked into this repo at `deploy/systemd/` as the
canonical source of truth. To install on a fresh host:

```bash
sudo cp deploy/systemd/kalshibot3.service           /etc/systemd/system/
sudo cp deploy/systemd/kalshibot3-dashboard.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/kalshibot3*.service
sudo systemctl daemon-reload
sudo systemctl enable --now kalshibot3.service kalshibot3-dashboard.service
```

## Hardening applied

Both units include:

| Setting | Value | Purpose |
|---|---|---|
| `Restart` | `on-failure` | Auto-restart on crash |
| `RestartSec` | `5` | 5s pause before restart |
| `StartLimitBurst` | `5` | Stop after 5 failures... |
| `StartLimitIntervalSec` | `60` | ...within 60s (crash-loop cap) |
| `ProtectSystem` | `strict` | Read-only `/usr`, `/boot`, `/efi` |
| `ReadWritePaths` | `/var/www/lutz-bot` | Only path we can write |
| `ProtectHome` | `true` | `/home`, `/root` invisible |
| `NoNewPrivileges` | `true` | Can't escalate via setuid |
| `PrivateTmp` | `true` | Isolated `/tmp` namespace |
| `ProtectKernelTunables` | `true` | `/proc/sys`, `/sys` read-only |
| `ProtectKernelModules` | `true` | Can't load kernel modules |
| `ProtectControlGroups` | `true` | cgroup hierarchy read-only |

Resource ceilings (per unit):

| Unit | MemoryMax | CPUQuota | TasksMax |
|---|---|---|---|
| backend | 512M | 50% | 200 |
| dashboard | 1G | 75% | 400 |

Combined worst-case: 1.5 GB RAM, 125% of one CPU core. Bounded.

## Day-to-day commands

```bash
./start.sh                              # restart both, show status
systemctl status kalshibot3             # backend status + recent logs
systemctl status kalshibot3-dashboard
journalctl -u kalshibot3 -f             # live backend logs
journalctl -u kalshibot3-dashboard -f   # live dashboard rebuild logs
systemctl stop kalshibot3{,-dashboard}.service     # stop everything
systemctl reset-failed kalshibot3       # clear "failed" state after crash-loop
```

## Verified single-instance guarantees

Tested 2026-05-26:

1. **Exactly one backend process** at any time (`pgrep -c uvicorn` returns 1)
2. **`systemctl start` on a running unit is a no-op** — same PID, no
   duplicate spawned
3. **`kill -9 <backend>` triggers auto-restart within ~5s** with a new PID;
   process count stays at 1
4. **`/api/health` responds after auto-restart** — environment correct,
   Kalshi auth still working
5. **Watcher still rebuilds on source-file save** (`built in ~200ms` logged
   to journal)
6. **Other services (nginx, postgresql, ssh) unaffected** before/after
   install — `is-active` reports the same state on each

## Removal (if ever needed)

Fully reversible:

```bash
sudo systemctl disable --now kalshibot3.service kalshibot3-dashboard.service
sudo rm /etc/systemd/system/kalshibot3.service
sudo rm /etc/systemd/system/kalshibot3-dashboard.service
sudo systemctl daemon-reload
```

Nothing else on the box is affected.
