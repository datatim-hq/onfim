#!/bin/sh
# Dependencies are pinned in lock files with hashes; the image installs them with pip --require-hashes. The lock is
# built in the same Python image as the Dockerfile (by digest). Versions already in the lock are kept (pip-compile
# without --upgrade); to move a library, change requirements*.txt and run this again. Optional constraints file
# (for example `pip freeze` of a running image): tools/lock.sh [constraints.txt]
set -eu
cd "$(dirname "$0")/.."
IMG=$(sed -n 's/^FROM \(python:[^ ]*\).*/\1/p' Dockerfile | head -1)
C1=${1:+-c /src/$1}
PC="pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --resolver=backtracking"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/src" -w /src "$IMG" sh -ec "
  pip install -q --user --no-warn-script-location --root-user-action=ignore pip-tools==7.5.1 && export PATH=/tmp/.local/bin:\$PATH
  $PC $C1 -o requirements.lock requirements.txt
  $PC -c requirements.lock -o requirements-dev.lock requirements-dev.txt"
# the header shows this command, not pip-compile's internal line with container paths
sed -i 's|^#    pip-compile .*|#    tools/lock.sh|; s|^#   -c /src/.*|#   constraints used when building the lock|' requirements*.lock
