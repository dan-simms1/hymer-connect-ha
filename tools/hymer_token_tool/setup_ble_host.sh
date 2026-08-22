#!/bin/sh
# Rebuild a LibreELEC/Kodi Pi as a BLE host for the HYMER SCU.
#
# Every step here was a blocker discovered the hard way on 2026-08-22.
#
#   scp setup_ble_host.sh root@<pi>:/storage/ && ssh root@<pi> sh /storage/setup_ble_host.sh
#
# Afterwards, from the repository root on your workstation:
#   tar czf - tools/hymer_token_tool/hymer_token_tool | ssh root@<pi> 'cd /storage/hymer && tar xzf -'
#   scp tools/hymer_token_tool/{dbus_pair.py,bond_test.py,full_pair.sh} root@<pi>:/storage/hymer/
#   scp <ha>/custom_components/hymer_connect_metadata/data/oauth_client.json \
#       root@<pi>:/storage/hymer/custom_components/hymer_connect_metadata/data/
set -e
umask 077

ROOT=/storage/hymer
LIBS=/storage/hymer_libs

echo "== 0. Platform check =="
# The wheel manifest below is for exactly this interpreter and architecture.
PYTAG=$(python3 -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}")')
ARCH=$(uname -m)
if [ "$PYTAG" != "cp311" ] || [ "$ARCH" != "aarch64" ]; then
  echo "   manifest is pinned for cp311/aarch64; this host is $PYTAG/$ARCH" >&2
  echo "   regenerate the manifest for this platform before continuing" >&2
  exit 2
fi
echo "   $PYTAG/$ARCH OK"

echo "== 1. Enable Bluetooth =="
# LibreELEC gates bluetooth.service behind a flag file its Kodi settings addon
# creates. Without it the unit is skipped and hci0 never gets an address.
mkdir -p /storage/.cache/services
touch /storage/.cache/services/bluez.conf
systemctl start bluetooth || true
sleep 4
systemctl is-active bluetooth >/dev/null || { echo "   bluetooth.service did not start" >&2; exit 3; }
(printf "power on\n"; sleep 3) | bluetoothctl >/dev/null 2>&1 || true
(printf "show\n"; sleep 2) | bluetoothctl 2>/dev/null | grep -E "Controller|Powered" || true

echo "== 2. Clock =="
# A wrong clock fails every HTTPS download with a confusing certificate error.
date
YEAR=$(date +%Y)
if [ "$YEAR" -lt 2026 ]; then
  echo "   clock looks wrong; fix it first: date -u -s 'YYYY-MM-DD HH:MM:SS'" >&2
  exit 4
fi

echo "== 3. Python deps: pinned, hash-verified wheels =="
# LibreELEC ships Python stripped of sources, so pip/ensurepip/get-pip all die.
# Wheels are zips: fetch exactly these files, verify SHA-256, unpack.
mkdir -p "$LIBS"
python3 - "$LIBS" <<'PY'
import hashlib, io, sys, urllib.request, zipfile
target = sys.argv[1]
MANIFEST = """
bleak-3.0.2-py3-none-any.whl 39092feb9e83f1df5ad2f88e837723c7211c982ce9e9cda6235104bc2ebe0d0d https://files.pythonhosted.org/packages/26/54/05aceb9cd80073805b3ed8522e3196e8cb22f70e741873fa51406c31f4e7/bleak-3.0.2-py3-none-any.whl
dbus_fast-5.0.22-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl 846f9a6602b4383f989201f7851459fb225a8912cd24b38e63894748545c3040 https://files.pythonhosted.org/packages/ec/84/dfb014de75a3a854dccaae1cce8f840e4312e3efc781768eedd60d25d9ef/dbus_fast-5.0.22-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl
typing_extensions-4.16.0-py3-none-any.whl 481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8 https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl
async_timeout-5.0.1-py3-none-any.whl 39e3809566ff85354557ec2398b55e096c8364bacac9405a7a1fa429e77fe76c https://files.pythonhosted.org/packages/fe/ba/e2081de779ca30d473f21f5b30e0e737c438205440784c7dfc81efc2b029/async_timeout-5.0.1-py3-none-any.whl
aiohttp-3.14.3-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl d6088ec9894113802bddb3c09e974929aed2c7b3a8c456219b8aab4481f1a239 https://files.pythonhosted.org/packages/9e/8d/a71c6f2db52ac1ed142b133f7feddaa6b70539c3f4de24d7e226c95b794c/aiohttp-3.14.3-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl
aiohappyeyeballs-2.7.1-py3-none-any.whl 9243213661e29250eb41368e5daa826fc017156c3b8a11440826b2e3ed376472 https://files.pythonhosted.org/packages/71/43/1947f06babed6b3f1d7f38b0c767f52df66bfb2bc10b468c4a7de9eceff2/aiohappyeyeballs-2.7.1-py3-none-any.whl
aiosignal-1.4.0-py3-none-any.whl 053243f8b92b990551949e63930a839ff0cf0b0ebbe0597b0f3fb19e1a0fe82e https://files.pythonhosted.org/packages/fb/76/641ae371508676492379f16e2fa48f4e2c11741bd63c48be4b12a6b09cba/aiosignal-1.4.0-py3-none-any.whl
attrs-26.1.0-py3-none-any.whl c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309 https://files.pythonhosted.org/packages/64/b4/17d4b0b2a2dc85a6df63d1157e028ed19f90d4cd97c36717afef2bc2f395/attrs-26.1.0-py3-none-any.whl
frozenlist-1.8.0-py3-none-any.whl 0c18a16eab41e82c295618a77502e17b195883241c563b00f0aa5106fc4eaa0d https://files.pythonhosted.org/packages/9a/9a/e35b4a917281c0b8419d4207f4334c8e8c5dbf4f3f5f9ada73958d937dcc/frozenlist-1.8.0-py3-none-any.whl
multidict-6.7.1-py3-none-any.whl 55d97cc6dae627efa6a6e548885712d4864b81110ac76fa4e534c03819fa4a56 https://files.pythonhosted.org/packages/81/08/7036c080d7117f28a4af526d794aab6a84463126db031b007717c1a6676e/multidict-6.7.1-py3-none-any.whl
propcache-0.5.2-py3-none-any.whl be1ddfcbb376e3de5d2e2db1d58d6d67463e6b4f9f040c000de8e300295465fe https://files.pythonhosted.org/packages/3a/ed/1cdcab6ba3d6ab7feca11fc14f0eeea80755bb53ef4e892079f31b10a25f/propcache-0.5.2-py3-none-any.whl
yarl-1.24.5-py3-none-any.whl a33700d13d9b7d84fd10947b09ff69fb9a792e519c8cb9764a3ca70baa6c23a7 https://files.pythonhosted.org/packages/61/02/962c1cbfc401a30c1d034dc67ff395f64b52302c6d62de556c1fca99acc0/yarl-1.24.5-py3-none-any.whl
idna-3.19-py3-none-any.whl 815e7be7a7806d54abb586dc943addc79e8b2ee16915059658cbeff4b1b43bf4 https://files.pythonhosted.org/packages/57/b0/0e52c878c53f245edd3a11020f20979b3f490f245af532c7cae3027754b5/idna-3.19-py3-none-any.whl
"""
for line in MANIFEST.strip().splitlines():
    name, sha, url = line.split()
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        print(f"   HASH MISMATCH for {name}: expected {sha} got {got}", file=sys.stderr)
        sys.exit(5)
    zipfile.ZipFile(io.BytesIO(data)).extractall(target)
    print("   verified + installed", name)
PY

echo "== 4. Layout the tool expects =="
# api.py computes REPO_ROOT as parents[3], so the package must live at
# <root>/tools/hymer_token_tool/hymer_token_tool and the gitignored
# oauth_client.json at <root>/custom_components/hymer_connect_metadata/data.
mkdir -p "$ROOT/tools/hymer_token_tool" "$ROOT/custom_components/hymer_connect_metadata/data"
chmod 700 "$ROOT" "$ROOT/custom_components/hymer_connect_metadata/data"

echo "== 5. Verify deps =="
PYTHONPATH="$LIBS" python3 -c "import bleak, dbus_fast, aiohttp; print('   bleak, dbus_fast, aiohttp OK')"

echo
echo "Done. Now copy the tool, the field scripts and oauth_client.json as shown"
echo "in the header of this script, then put activation.txt and creds.ini in"
echo "$ROOT with mode 0600, and run full_pair.sh with SCU, COMPONENT_ID,"
echo "VALUE_ID and CONFIRM=1 set. PYTHONPATH must be $LIBS:$ROOT/tools/hymer_token_tool"
