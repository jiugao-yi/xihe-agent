#!/bin/sh
# xihe-agent deb postrm: remove the /usr/bin/xihe symlink we created.
set -e
rm -f /usr/bin/xihe
