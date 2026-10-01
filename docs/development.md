# Ontwikkeling

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q

npm install
npm test
```

- **Backend** (`tests/test_*.py`): alle host-paden (`/proc`, `/sys/block`, `/proc/self/mounts`,
  `disks.ini`) worden via de fixture `sd` naar een tijdelijke map omgeleid. Externe commando's
  (`hdparm`, `fatrace`) worden niet aangeroepen; de logica eromheen is los testbaar
  (`parse_hdparm`, `poll_once`, `handle`, `LINE`).
- **Frontend** (`tests/js/`): het dashboard draait in jsdom met een nagebootste `fetch`.

## Lokaal draaien zonder Unraid

Met nep-`hdparm` en een eigen `disks.ini` kun je de hele app op een laptop draaien:

```bash
mkdir -p /tmp/sd/{emhttp,data,bin}
printf '["disk1"]\ndevice="sda"\n' > /tmp/sd/emhttp/disks.ini
printf '#!/bin/sh\necho " drive state is:  active/idle"\n' > /tmp/sd/bin/hdparm && chmod +x /tmp/sd/bin/hdparm
PATH=/tmp/sd/bin:$PATH DATA_DIR=/tmp/sd/data EMHTTP_DIR=/tmp/sd/emhttp \
  WATCH=/tmp PORT=8089 python3 app/spindash.py
```

`fatrace` vereist root en een Linux-kernel; zonder `fatrace` logt de watcher een fout en blijft
de rest werken. Voor alleen de frontend: open `http://localhost:8089/?demo`.

## Op Unraid testen

```bash
docker build -t spin-dashboard:dev .
docker run -d --name spin-dashboard-dev --privileged --pid=host -p 8090:8089 \
  -v /mnt:/mnt:ro,slave -v /var/local/emhttp:/emhttp:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /mnt/user/appdata/spin-dashboard-dev:/data \
  spin-dashboard:dev
docker logs -f spin-dashboard-dev
```

Let op: draai niet twee instanties met dezelfde `DATA_DIR`.
