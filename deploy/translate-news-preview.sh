#!/usr/bin/env bash
# Isolated VPS translation runner. It has no SMTP or report-generation options.
set -eu
cd /opt/bttn-nvidia-preview
set -a
. /etc/bttn/bttn.env
set +a
mkdir -p /var/lib/bttn/nvidia-preview
output_dir=$(mktemp -d /var/lib/bttn/nvidia-preview/run.XXXXXXXX)
exec /opt/bttn/.venv/bin/python -m bttn.translation_service translate \
    --cache-dir /var/lib/bttn/state/translations --output-dir "$output_dir" "$@"
