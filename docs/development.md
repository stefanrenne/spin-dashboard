# Development

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q

npm install
npm test
```

- **Backend** (`tests/test_*.py`): all host paths (`/proc`, `/sys/block`, `/proc/self/mounts`,
  `disks.ini`) and the data directory are redirected to a temporary folder by the `sd` fixture,
  which also pins `TZ` to `Europe/Amsterdam` so day boundaries are predictable. External commands
  (`hdparm`, `fatrace`) are not called; the logic around them can be tested on its own
  (`parse_hdparm`, `poll_once`, `handle`, `LINE`, `migrate`).
- **Frontend** (`tests/js/`): the dashboard runs in jsdom with a mocked `fetch` that serves
  `days.json` and day files.

## Running locally without Unraid

With a fake `hdparm` and your own `disks.ini` you can run the whole app on a laptop:

```bash
mkdir -p /tmp/sd/{emhttp,data,bin}
printf '["disk1"]\ndevice="sda"\n' > /tmp/sd/emhttp/disks.ini
printf '#!/bin/sh\necho " drive state is:  active/idle"\n' > /tmp/sd/bin/hdparm && chmod +x /tmp/sd/bin/hdparm
PATH=/tmp/sd/bin:$PATH DATA_DIR=/tmp/sd/data EMHTTP_DIR=/tmp/sd/emhttp \
  WATCH=/tmp PORT=8089 python3 app/spindash.py
```

`fatrace` needs root and a Linux kernel; without `fatrace` the watcher logs an error and the rest
keeps working. Old-format files (`spin.csv`, `who.csv`, …) placed in `DATA_DIR` are migrated on
start, which is a quick way to try the dashboard on real data. For the frontend only: open
`http://localhost:8089/?demo`. Force a language with `?lang=de` (en, nl, fr, de, es); otherwise the
UI follows the browser language or the last chosen language (`localStorage`).

## Testing on Unraid

```bash
docker build -t spin-dashboard:dev .
docker run -d --name spin-dashboard-dev --privileged --pid=host -p 8090:8089 \
  -v /mnt:/mnt:ro,slave -v /var/local/emhttp:/emhttp:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /mnt/user/appdata/spin-dashboard-dev:/data \
  spin-dashboard:dev
docker logs -f spin-dashboard-dev
```

Note: do not run two instances with the same `DATA_DIR`.
