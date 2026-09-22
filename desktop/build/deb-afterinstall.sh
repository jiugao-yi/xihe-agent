#!/bin/sh
# xihe-agent deb postinst: expose the embedded CLI as /usr/bin/xihe.
# Runs as root during install/upgrade; ln -sf is idempotent.
set -e
ln -sf /opt/xihe-agent/resources/bin/xihe/xihe /usr/bin/xihe
