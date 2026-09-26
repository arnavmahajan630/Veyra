#!/bin/bash
# Wrap the official manager entrypoint so VEYRA's localfile block and rules are in place
# before ossec starts. Idempotent: re-running a container never duplicates the block.
set -euo pipefail

OSSEC_CONF=/var/ossec/etc/ossec.conf
mkdir -p /sinks/wazuh
touch /sinks/wazuh/veyra.ndjson

if [ -f "$OSSEC_CONF" ] && ! grep -q "/sinks/wazuh/veyra.ndjson" "$OSSEC_CONF"; then
  echo "[veyra] adding the NDJSON localfile to ossec.conf"
  cat /veyra/veyra_localfile.xml >> "$OSSEC_CONF"
fi

exec /init
