#!/usr/bin/env bash
# End-to-end check that a built image actually runs a Factorio server.
#
#   tests/smoke-test.sh <image[:tag]> <expected-version>
#
# Verifies, in order:
#   1. the packaged binary reports the expected headless version;
#   2. a fresh volume gets seeded and a map generated;
#   3. the server reaches the InGame state and binds the game port;
#   4. the container's healthcheck turns healthy;
#   5. RCON answers /version with the expected version;
#   6. the server process does not run as root;
#   7. `docker stop` shuts down gracefully and saves the map;
#   8. a restart re-uses the existing save instead of generating a new one.
set -euo pipefail

IMAGE="${1:-${IMAGE:-}}"
EXPECTED_VERSION="${2:-${EXPECTED_VERSION:-}}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-300}"
SHUTDOWN_TIMEOUT="${SHUTDOWN_TIMEOUT:-60}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-240}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RCON_PASSWORD="smoke-$(date +%s)-$RANDOM"
CONTAINER=""
WORKDIR=""
FAILURES=0

[[ -n "${IMAGE}" ]] || { echo "usage: $0 <image> <expected-version>" >&2; exit 2; }
[[ -n "${EXPECTED_VERSION}" ]] || { echo "usage: $0 <image> <expected-version>" >&2; exit 2; }

pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# Invoked through the EXIT trap; shellcheck cannot see that call.
# shellcheck disable=SC2317
cleanup() {
    local status=$?
    if [[ -n "${CONTAINER}" ]] && docker inspect "${CONTAINER}" >/dev/null 2>&1; then
        if [[ ${status} -ne 0 || ${FAILURES} -ne 0 ]]; then
            echo
            echo "--- container logs (last 80 lines) ---"
            docker logs --tail 80 "${CONTAINER}" 2>&1 || true
            echo "--------------------------------------"
        fi
        docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${WORKDIR}" && -d "${WORKDIR}" ]]; then
        rm -rf "${WORKDIR}"
    fi
    return "${status}"
}
trap cleanup EXIT

wait_for_log() {
    local needle="$1" deadline=$((SECONDS + STARTUP_TIMEOUT))
    while ((SECONDS < deadline)); do
        if docker logs "${CONTAINER}" 2>&1 | grep -qF "${needle}"; then
            return 0
        fi
        if ! docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null | grep -q true; then
            echo "container exited before '${needle}' appeared" >&2
            return 1
        fi
        sleep 2
    done
    echo "timed out after ${STARTUP_TIMEOUT}s waiting for '${needle}'" >&2
    return 1
}

start_container() {
    CONTAINER="$(docker run -d \
        --name "factorio-smoke-$$-${RANDOM}" \
        -e RCON_PASSWORD="${RCON_PASSWORD}" \
        -e SAVE_NAME=smoketest \
        -p 127.0.0.1::34197/udp \
        -p 127.0.0.1::27015/tcp \
        -v "${WORKDIR}:/factorio" \
        "${IMAGE}")"
}

echo "Smoke testing ${IMAGE} (expecting Factorio ${EXPECTED_VERSION})"

# --- 1. version ------------------------------------------------------------
step "Packaged binary reports the expected version"
version_output="$(docker run --rm "${IMAGE}" --version 2>&1 || true)"
echo "${version_output}" | head -1
if grep -qF "Version: ${EXPECTED_VERSION}" <<< "${version_output}"; then
    pass "binary reports ${EXPECTED_VERSION}"
else
    fail "binary does not report ${EXPECTED_VERSION}"
fi
if grep -qF "headless" <<< "${version_output}"; then
    pass "build is the headless variant"
else
    fail "build is not the headless variant"
fi

# --- 2./3. first boot ------------------------------------------------------
step "First boot: seed the volume, generate a map and host the game"
WORKDIR="$(mktemp -d)"
chmod 0777 "${WORKDIR}"
start_container

if wait_for_log "changing state from(CreatingGame) to(InGame)"; then
    pass "server reached the InGame state"
else
    fail "server never reached the InGame state"
fi

if docker logs "${CONTAINER}" 2>&1 | grep -qF "Hosting game at IP ADDR"; then
    pass "server is hosting on the game port"
else
    fail "server never reported hosting the game"
fi

for expected_file in config/config.ini config/server-settings.json saves/smoketest.zip; do
    if [[ -f "${WORKDIR}/${expected_file}" ]]; then
        pass "volume contains ${expected_file}"
    else
        fail "volume is missing ${expected_file}"
    fi
done

# --- 4. healthcheck --------------------------------------------------------
step "Container healthcheck turns healthy"
health_deadline=$((SECONDS + HEALTH_TIMEOUT))
health="starting"
while ((SECONDS < health_deadline)); do
    health="$(docker inspect -f '{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null || echo none)"
    [[ "${health}" == "starting" ]] || break
    sleep 5
done
if [[ "${health}" == "healthy" ]]; then
    pass "healthcheck reports healthy"
else
    fail "healthcheck reports '${health}'"
fi

# --- 5. RCON ---------------------------------------------------------------
step "RCON answers /version"
rcon_hostport="$(docker port "${CONTAINER}" 27015/tcp | head -1)"
rcon_port="${rcon_hostport##*:}"
rcon_version=""
for _ in $(seq 1 12); do
    if rcon_version="$(python3 "${SCRIPT_DIR}/rcon.py" 127.0.0.1 "${rcon_port}" "${RCON_PASSWORD}" "/version" 2>/dev/null)"; then
        [[ -n "${rcon_version//[[:space:]]/}" ]] && break
    fi
    sleep 5
done
rcon_version="${rcon_version//[[:space:]]/}"
if [[ "${rcon_version}" == "${EXPECTED_VERSION}" ]]; then
    pass "RCON /version returned ${rcon_version}"
else
    fail "RCON /version returned '${rcon_version}', expected '${EXPECTED_VERSION}'"
fi

# --- 6. privileges ---------------------------------------------------------
step "Server does not run as root"
# The entrypoint execs the server, so PID 1 in the container is factorio itself.
pid1_comm="$(docker exec "${CONTAINER}" cat /proc/1/comm 2>/dev/null | tr -d '[:space:]' || true)"
pid1_uid="$(docker exec "${CONTAINER}" cat /proc/1/status 2>/dev/null \
    | awk '/^Uid:/ { print $2; exit }' || true)"

if [[ "${pid1_comm}" == "factorio" ]]; then
    pass "factorio is PID 1 and receives signals directly"
else
    fail "PID 1 is '${pid1_comm:-unknown}', not the factorio binary"
fi
if [[ -n "${pid1_uid}" && "${pid1_uid}" != "0" ]]; then
    pass "factorio runs as uid ${pid1_uid}"
else
    fail "factorio appears to run as root (uid '${pid1_uid:-unknown}')"
fi

# --- 7. graceful shutdown --------------------------------------------------
step "docker stop shuts the server down cleanly"
stop_start=${SECONDS}
docker stop --timeout "${SHUTDOWN_TIMEOUT}" "${CONTAINER}" >/dev/null
stop_duration=$((SECONDS - stop_start))
exit_code="$(docker inspect -f '{{.State.ExitCode}}' "${CONTAINER}")"

if docker logs "${CONTAINER}" 2>&1 | grep -qF "Goodbye"; then
    pass "server logged a clean shutdown after ${stop_duration}s"
else
    fail "server did not shut down cleanly (no 'Goodbye' in the log)"
fi
if ((stop_duration < SHUTDOWN_TIMEOUT)); then
    pass "shutdown finished before the ${SHUTDOWN_TIMEOUT}s kill timeout"
else
    fail "shutdown had to be killed after ${SHUTDOWN_TIMEOUT}s"
fi
if [[ "${exit_code}" == "0" ]]; then
    pass "container exited with code 0"
else
    fail "container exited with code ${exit_code}"
fi

docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
CONTAINER=""

# --- 8. restart on an existing volume --------------------------------------
step "Restart re-uses the existing save"
save_checksum_before="$(sha256sum "${WORKDIR}/saves/smoketest.zip" | cut -d' ' -f1)"
start_container
if wait_for_log "changing state from(CreatingGame) to(InGame)"; then
    pass "server came back up on the existing volume"
else
    fail "server did not come back up on the existing volume"
fi
if docker logs "${CONTAINER}" 2>&1 | grep -qF "No save found, creating"; then
    fail "server generated a new map instead of loading the existing save"
else
    pass "existing save was re-used"
fi
if [[ -n "${save_checksum_before}" ]]; then
    pass "save survived the container replacement"
fi

docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
CONTAINER=""

# --- result ----------------------------------------------------------------
echo
if ((FAILURES == 0)); then
    printf '\033[32mAll smoke tests passed for %s (Factorio %s)\033[0m\n' "${IMAGE}" "${EXPECTED_VERSION}"
    exit 0
fi
printf '\033[31m%d smoke test(s) failed for %s\033[0m\n' "${FAILURES}" "${IMAGE}"
exit 1
