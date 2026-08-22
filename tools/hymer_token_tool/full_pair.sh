#!/bin/sh
# remove stale bond -> rescan -> bond -> app-level pair -> one confirmed write.
#
# Secrets never cross argv: the activation token goes in via
# --activation-token-file and the account password via --ini-file. Both files
# must be mode 0600. The vehicle address and the write target are required
# inputs, and nothing is actuated unless CONFIRM=1.
#
# Exit codes: 0 write accepted, 2 write rejected by the SCU, 3 not confirmed,
# 4 bad inputs, 5 bond never completed, 6 app-level pairing failed.
umask 077
ROOT="${ROOT:-/storage/hymer}"
export PYTHONPATH="${PYTHONPATH:-/storage/hymer_libs}:$ROOT/tools/hymer_token_tool"
ACT_FILE="${ACT_FILE:-$ROOT/activation.txt}"
CREDS="${CREDS:-$ROOT/creds.ini}"
TOKEN_OUT="${TOKEN_OUT:-$ROOT/remote-refresh.txt}"
SESSION_OUT="${SESSION_OUT:-$ROOT/pair-session.json}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-25}"

fail() { echo "error: $1" >&2; exit "$2"; }
mode_of() { python3 -c "import os,sys;print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])" "$1" 2>/dev/null; }
require_0600() {
  [ -f "$1" ] || fail "$1 does not exist" 4
  m=$(mode_of "$1")
  [ "$m" = "600" ] || fail "$1 must be mode 0600 (is $m)" 4
}

[ -n "$SCU" ] || fail "SCU=<ble-mac> is required" 4
[ -n "$COMPONENT_ID" ] && [ -n "$VALUE_ID" ] || fail "COMPONENT_ID and VALUE_ID are required" 4
[ "$CONFIRM" = "1" ] || fail "refusing to actuate the vehicle without CONFIRM=1" 3
require_0600 "$ACT_FILE"
require_0600 "$CREDS"
cd "$ROOT/tools/hymer_token_tool" || fail "tool not found under $ROOT/tools/hymer_token_tool" 4
rm -f "$TOKEN_OUT" "$SESSION_OUT"

rescan() {
  python3 - <<'PY'
import asyncio, os, sys
sys.path.insert(0, os.environ.get("PYTHONPATH", "").split(":")[0])
from bleak import BleakScanner
async def m():
    d = await BleakScanner.discover(timeout=8.0)
    print("  rescan saw:", [x.address for x in d if (x.name or "").lower().startswith("hymer")] or "nothing")
asyncio.run(m())
PY
}

i=0
bonded=0
while [ "$i" -lt "$MAX_ATTEMPTS" ]; do
  i=$((i+1))
  # Removing a bond deletes BlueZ's device object, so rescan before Pair().
  (printf "remove %s\n" "$SCU"; sleep 2) | bluetoothctl >/dev/null 2>&1
  rescan
  if SCU="$SCU" python3 "$ROOT/dbus_pair.py"; then
    bonded=1
    echo "===== BONDED on attempt $i at $(date +%H:%M:%S) ====="
    break
  fi
  echo "attempt $i $(date +%H:%M:%S): not bonded"
done
[ "$bonded" = "1" ] || fail "bond never completed in $i attempts" 5

echo "----- app-level pairing -----"
# Only "Press Enter" prompts remain on stdin; the token comes from the file.
if ! printf "\n\n\n" | python3 -m hymer_token_tool mint-remote-refresh \
      --ini-file "$CREDS" \
      --activation-token-file "$ACT_FILE" \
      --identifier "$SCU" \
      --mobile-device-name "${DEVICE_NAME:-ble-host}" \
      --token-file "$TOKEN_OUT" \
      --session-file "$SESSION_OUT"; then
  fail "mint-remote-refresh failed; not sending any write" 6
fi
[ -s "$TOKEN_OUT" ] || fail "pairing reported success but wrote no token; not sending any write" 6

echo "----- setValues -----"
SCU="$SCU" COMPONENT_ID="$COMPONENT_ID" VALUE_ID="$VALUE_ID" CONFIRM=1 \
  WR="${WR:-1}" BOND=0 python3 "$ROOT/bond_test.py"
