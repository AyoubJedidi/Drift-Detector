#!/usr/bin/env bash
# set -euo pipefail

NS="drift-test"
FIXTURES="tests/fixtures"
RESULT="/tmp/drift-result.json"

reset_ns() {
  kubectl delete ns "$NS" --ignore-not-found --wait=true >/dev/null
  kubectl create ns "$NS" >/dev/null
}

apply_baseline() {
  kubectl apply -n "$NS" -f "$FIXTURES/$1/" >/dev/null
  kubectl wait --for=condition=available --timeout=60s \
    -n "$NS" deploy --all 2>/dev/null || true
}

# Run the tool, capture JSON output and exit code
run_scan() {
  local fixture=$1; shift
  drift-detect scan \
    --source "$FIXTURES/$fixture" \
    --namespace "$NS" \
    --output json \
    "$@" > "$RESULT" || echo "EXIT=$?"
}

# Assertion helpers
assert_count() {
  local severity=$1 expected=$2
  local actual
  actual=$(jq -r ".summary.$severity // 0" "$RESULT")
  [[ "$actual" == "$expected" ]] || {
    echo "FAIL: expected $severity=$expected, got $actual"
    return 1
  }
  echo "OK: $severity=$actual"
}

assert_path_drifted() {
  local path=$1
  jq -e --arg p "$path" '.drifts[] | select(.path | contains($p))' "$RESULT" >/dev/null || {
    echo "FAIL: expected drift on path $path"
    return 1
  }
  echo "OK: drift on $path"
}

assert_exit() {
  local expected=$1 actual=${2:-0}
  [[ "$actual" == "$expected" ]] || {
    echo "FAIL: expected exit $expected, got $actual"
    return 1
  }
  echo "OK: exit=$actual"
}