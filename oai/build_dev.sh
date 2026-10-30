#!/bin/bash
# Incremental build of the OAI gNB/nrUE binaries inside the oai-dev container (source mounted at /oai-ran).
set -e
sg docker -c "docker exec oai-dev bash -c '
  set -e; mkdir -p /oai-ran/build_nrlink && cd /oai-ran/build_nrlink
  [ -f build.ninja ] || cmake -GNinja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DAVX512=OFF .. > cmake.log 2>&1
  ninja -j4 nr-softmodem nr-uesoftmodem rfsimulator ldpc params_libconfig dfts coding 2>&1 | tail -25
'"
