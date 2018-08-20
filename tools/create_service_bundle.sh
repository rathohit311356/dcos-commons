#!/usr/bin/env bash

set -eu -o pipefail

function usage () {
  echo 'Usage: ./create_service_bundle.sh <package name> <service name> [<proceed>]'
  echo
  echo 'Example: ./create_service_bundle.sh cassandra /prod/cassandra yes'
}

if [ "${#}" -lt 2 ]; then
  echo -e "create_service_bundle.sh needs at least 2 arguments but was given ${#}\\n"
  usage
  exit 1
fi

readonly REQUIREMENTS='docker'

for requirement in ${REQUIREMENTS}; do
  if ! [[ -x $(command -v "${requirement}") ]]; then
    echo "You need to install '${requirement}' to run this script"
    exit 1
  fi
done

readonly DOCKER_IMAGE="mpereira/dcos-commons:diagnostics"
readonly SCRIPT_PATH="/dcos-commons-dist/tools/create_service_bundle.py"

readonly PACKAGE_NAME="${1:-}"
readonly SERVICE_NAME="${2:-}"
readonly PROCEED="${3:-}"

docker run --rm "${DOCKER_IMAGE}" \
       bash -c "python3 ${SCRIPT_PATH} \
                        --package-name ${PACKAGE_NAME} \
                        --service-name ${SERVICE_NAME}
                        --yes"
