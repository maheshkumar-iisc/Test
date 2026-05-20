#!/usr/bin/env bash
# End-to-end smoke test: baseline poll, inject failures, observe, reset.
# Assumes fire-sim is already running on :9090 with 100 buildings on 5020+.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
POLL=.venv/bin/fire-sim-poll
API=localhost:9090

echo "=== baseline: 100 buildings x 5 rounds ==="
timeout 30 $POLL --buildings 100 --rounds 5 --interval 1.0

echo
echo "=== inject failures ==="
curl -s -X POST $API/scenarios/storm           -H 'content-type: application/json' -d '{"count":25}' >/dev/null && echo "  storm: 25 alarms"
curl -s -X POST $API/scenarios/random-offline  -H 'content-type: application/json' -d '{"count":5}'  >/dev/null && echo "  offline: 5"
curl -s -X POST $API/scenarios/random-freeze   -H 'content-type: application/json' -d '{"count":5}'  >/dev/null && echo "  frozen: 5"
sleep 2
timeout 30 $POLL --buildings 100 --rounds 1

echo
echo "=== state summary ==="
curl -s $API/buildings | $PY -c "
import json, sys
b = json.load(sys.stdin)
fire  = [x['id'] for x in b if x['fire']==1]
fault = [x['id'] for x in b if x['fire']==2]
froz  = [x['id'] for x in b if x['frozen']]
off   = [x['id'] for x in b if x['offline']]
print(f'  alarm={len(fire)} fault={len(fault)} frozen={len(froz)} offline={len(off)}')
print(f'  offline ids: {off}')
print(f'  frozen ids:  {froz}')
"

echo
echo "=== reset and re-poll ==="
curl -s -X POST $API/scenarios/reset >/dev/null
sleep 1
timeout 30 $POLL --buildings 100 --rounds 1
