#!/bin/bash
# Wrap the official manager entrypoint so VEYRA's localfile block and rules are in place
# before ossec starts. Idempotent: re-running a container never duplicates the block.
set -euo pipefail

OSSEC_CONF=/var/ossec/etc/ossec.conf
# Do not create /sinks/wazuh/veyra.ndjson here: this container runs as root, and the
# file's writer is the router (or `make up` on the host). Wazuh tolerates it missing and
# picks it up when it appears.
mkdir -p /sinks/wazuh

# /var/ossec/etc is a named volume, so copy VEYRA's rules in on every start; the file is
# small and this keeps the rules in git as the source of truth (A6 owns it).
if [ -d /var/ossec/etc/rules ]; then
  cp /veyra/veyra_rules.xml /var/ossec/etc/rules/veyra_rules.xml
  chown root:wazuh /var/ossec/etc/rules/veyra_rules.xml 2>/dev/null || true
fi

if [ -f "$OSSEC_CONF" ] && ! grep -q "/sinks/wazuh/veyra.ndjson" "$OSSEC_CONF"; then
  echo "[veyra] adding the NDJSON localfile to ossec.conf"
  cat /veyra/veyra_localfile.xml >> "$OSSEC_CONF"
fi

exec /init
