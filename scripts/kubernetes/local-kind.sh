#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKTREE_HASH="$(printf '%s' "${ROOT_DIR}" | sha256sum | cut -c1-12)"
DEFAULT_CLUSTER_NAME="bifrost-${WORKTREE_HASH}"
STATE_DIR="${BIFROST_KIND_STATE_DIR:-/tmp/bifrost-k8s-${WORKTREE_HASH}}"
BIN_DIR="${STATE_DIR}/bin"
KUBECONFIG_PATH="${STATE_DIR}/kubeconfig"
CLUSTER_NAME="${BIFROST_KIND_CLUSTER_NAME:-${DEFAULT_CLUSTER_NAME}}"
KIND_CONTEXT="kind-${CLUSTER_NAME}"
NAMESPACE="bifrost-local"
IMAGE_NAME="${BIFROST_K8S_IMAGE:-bifrost-api:kind-${WORKTREE_HASH}}"
KIND_VERSION="${BIFROST_KIND_VERSION:-v0.24.0}"
KEDA_VERSION="${BIFROST_KEDA_VERSION:-2.16.1}"
KIND_BIN="${BIN_DIR}/kind-${KIND_VERSION}"

usage() {
  cat <<USAGE
Usage: $0 <up|status|down|collect|experiment|build-experiment|kubectl -- <args>>

Environment overrides:
  BIFROST_KIND_CLUSTER_NAME  Cluster name (default: bifrost-<worktree-hash>)
  BIFROST_KIND_STATE_DIR     Runtime state dir (default: /tmp/bifrost-k8s-<worktree-hash>)
  BIFROST_K8S_IMAGE          API image tag to deploy (default: bifrost-api:kind-<worktree-hash>)
  BIFROST_KIND_SKIP_BUILD    Set to 1 to skip docker build and load BIFROST_K8S_IMAGE
  BIFROST_KIND_VERSION       Kind version (default: v0.24.0)
  BIFROST_KEDA_VERSION       KEDA version without leading v (default: 2.16.1)
  BIFROST_KIND_BUILD_JOBS    Set to 1 to opt into Kubernetes App build Jobs
USAGE
}

kubectl_local() {
  kubectl --kubeconfig "${KUBECONFIG_PATH}" --context "${KIND_CONTEXT}" "$@"
}

download_kind() {
  mkdir -p "${BIN_DIR}"
  if [[ -x "${KIND_BIN}" ]]; then
    return
  fi

  local arch
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) echo "Unsupported architecture for kind: $(uname -m)" >&2; exit 1 ;;
  esac

  local os
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) echo "Unsupported OS for kind: $(uname -s)" >&2; exit 1 ;;
  esac

  local filename="kind-${os}-${arch}"
  local url="https://kind.sigs.k8s.io/dl/${KIND_VERSION}/${filename}"
  local checksum_url="${url}.sha256sum"
  local tmp="${KIND_BIN}.tmp"
  local checksum_file="${tmp}.sha256sum"
  curl --fail --location --silent --show-error --output "${tmp}" "${url}"
  curl --fail --location --silent --show-error --output "${checksum_file}" "${checksum_url}"
  local expected actual
  expected="$(awk '{print $1}' "${checksum_file}")"
  actual="$(sha256sum "${tmp}" | awk '{print $1}')"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "kind checksum mismatch for ${url}" >&2
    rm -f "${tmp}" "${checksum_file}"
    exit 1
  fi
  chmod +x "${tmp}"
  mv "${tmp}" "${KIND_BIN}"
  rm -f "${checksum_file}"
}

ensure_cluster() {
  download_kind
  mkdir -p "${STATE_DIR}"

  if "${KIND_BIN}" get clusters | grep -Fxq "${CLUSTER_NAME}"; then
    "${KIND_BIN}" export kubeconfig --name "${CLUSTER_NAME}" --kubeconfig "${KUBECONFIG_PATH}"
    return
  fi

  "${KIND_BIN}" create cluster \
    --name "${CLUSTER_NAME}" \
    --config "${ROOT_DIR}/k8s/local/kind-config.yaml" \
    --kubeconfig "${KUBECONFIG_PATH}"
}

write_rendered_manifests() {
  local rendered="${STATE_DIR}/rendered.yaml"
  mkdir -p "${STATE_DIR}"
  kubectl kustomize "${ROOT_DIR}/k8s/local" | awk -v image="${IMAGE_NAME}" '
    { gsub("bifrost-api:kind-local", image) }
    { print }
  ' > "${rendered}"
  echo "${rendered}"
}

render_with_image() {
  local source_file="$1"
  local rendered_file="$2"
  awk -v image="${IMAGE_NAME}" '
    { gsub("bifrost-api:kind-local", image) }
    { print }
  ' "${source_file}" > "${rendered_file}"
}

apply_local_manifest() {
  local source_file="$1"
  local rendered_file="${STATE_DIR}/$(basename "${source_file}")"
  render_with_image "${source_file}" "${rendered_file}"
  kubectl_local apply -f "${rendered_file}"
}

render_runtime_manifest() {
  local source_file="$1"
  local rendered_file="$2"
  local revision="$3"
  local build_jobs="${BIFROST_KIND_BUILD_JOBS:-}"
  awk -v image="${IMAGE_NAME}" -v revision="${revision}" -v build_jobs="${build_jobs}" '
    { gsub("bifrost-api:kind-local", image) }
    /^kind: / {
      kind=$2
    }
    kind == "Deployment" && /^  name: / {
      deployment=$2
    }
    build_jobs == "1" && deployment == "bifrost-scheduler" && $0 == "      automountServiceAccountToken: false" {
      print "      automountServiceAccountToken: true"
      print "      serviceAccountName: bifrost-build-controller"
      next
    }
    { print }
    build_jobs == "1" && (deployment == "bifrost-api" || deployment == "bifrost-scheduler") && $0 == "                name: bifrost-local-secrets" {
      print "            - configMapRef:"
      print "                name: bifrost-build-jobs-config"
    }
    /^        app.kubernetes.io\/name:/ {
      print "      annotations:"
      print "        bifrost.local/runtime-revision: \"" revision "\""
    }
  ' "${source_file}" > "${rendered_file}"
}

apply_runtime_manifest() {
  local source_file="$1"
  local revision="$2"
  local rendered_file="${STATE_DIR}/runtime-$(basename "${source_file}")"
  render_runtime_manifest "${source_file}" "${rendered_file}" "${revision}"
  kubectl_local apply -f "${rendered_file}"
}

apply_build_jobs_manifest() {
  if [[ "${BIFROST_KIND_BUILD_JOBS:-}" != "1" ]]; then
    return
  fi

  apply_local_manifest "${ROOT_DIR}/k8s/local/build-jobs.yaml"
}

build_and_load_image() {
  if [[ "${BIFROST_KIND_SKIP_BUILD:-}" == "1" ]]; then
    echo "Skipping docker build; loading ${IMAGE_NAME} into ${CLUSTER_NAME}"
    "${KIND_BIN}" load docker-image "${IMAGE_NAME}" --name "${CLUSTER_NAME}"
    return
  fi

  docker build \
    --file "${ROOT_DIR}/api/Dockerfile" \
    --tag "${IMAGE_NAME}" \
    "${ROOT_DIR}"
  "${KIND_BIN}" load docker-image "${IMAGE_NAME}" --name "${CLUSTER_NAME}"
}

install_keda() {
  kubectl_local apply --server-side -f "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml"
  kubectl_local rollout status deployment/keda-operator -n keda --timeout=180s
  kubectl_local rollout status deployment/keda-metrics-apiserver -n keda --timeout=180s
  kubectl_local rollout status deployment/keda-admission -n keda --timeout=180s
}

runtime_revision() {
  local rendered="$1"
  local image_id config_hash
  image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE_NAME}")"
  config_hash="$(sha256sum "${rendered}" | awk '{print $1}')"
  printf '%s' "${image_id}:${config_hash}" | sha256sum | cut -c1-16
}


collect_on_failure() {
  local exit_code="$1"
  trap - ERR
  echo "up failed; collecting diagnostics before exit" >&2
  if [[ -f "${KUBECONFIG_PATH}" ]]; then
    collect || true
  fi
  exit "${exit_code}"
}

wait_for_dependencies() {
  kubectl_local wait --for=condition=Ready pod -l app.kubernetes.io/name=postgres -n "${NAMESPACE}" --timeout=180s
  kubectl_local wait --for=condition=Available deployment/pgbouncer -n "${NAMESPACE}" --timeout=180s
  kubectl_local wait --for=condition=Ready pod -l app.kubernetes.io/name=rabbitmq -n "${NAMESPACE}" --timeout=180s
  kubectl_local wait --for=condition=Ready pod -l app.kubernetes.io/name=redis -n "${NAMESPACE}" --timeout=180s
  kubectl_local wait --for=condition=Ready pod -l app.kubernetes.io/name=seaweedfs -n "${NAMESPACE}" --timeout=180s
}

up() {
  trap 'collect_on_failure $?' ERR
  command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
  command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 1; }
  command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

  ensure_cluster
  build_and_load_image
  install_keda
  local rendered
  rendered="$(write_rendered_manifests)"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/namespace.yaml"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/config.yaml"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/postgres.yaml"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/pgbouncer.yaml"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/rabbitmq.yaml"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/redis.yaml"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/seaweedfs.yaml"
  wait_for_dependencies
  kubectl_local delete job/bifrost-init -n "${NAMESPACE}" --ignore-not-found
  apply_local_manifest "${ROOT_DIR}/k8s/local/init.yaml"
  kubectl_local wait --for=condition=complete job/bifrost-init -n "${NAMESPACE}" --timeout=300s
  apply_build_jobs_manifest
  apply_runtime_manifest "${ROOT_DIR}/k8s/local/bifrost.yaml" "$(runtime_revision "${rendered}")"
  kubectl_local apply -f "${ROOT_DIR}/k8s/local/keda.yaml"
  kubectl_local rollout status deployment/bifrost-api -n "${NAMESPACE}" --timeout=300s
  kubectl_local rollout status deployment/bifrost-scheduler -n "${NAMESPACE}" --timeout=180s
  kubectl_local rollout status deployment/bifrost-worker -n "${NAMESPACE}" --timeout=180s
  kubectl_local wait --for=condition=Ready=True scaledobject/bifrost-worker -n "${NAMESPACE}" --timeout=180s
  trap - ERR
  status
}

status() {
  download_kind
  if [[ ! -f "${KUBECONFIG_PATH}" ]] && "${KIND_BIN}" get clusters | grep -Fxq "${CLUSTER_NAME}"; then
    "${KIND_BIN}" export kubeconfig --name "${CLUSTER_NAME}" --kubeconfig "${KUBECONFIG_PATH}"
  fi

  echo "Cluster: ${CLUSTER_NAME}"
  echo "Kubeconfig: ${KUBECONFIG_PATH}"
  echo "Context: ${KIND_CONTEXT}"
  echo "State: ${STATE_DIR}"
  echo "API port-forward: $0 kubectl -- port-forward -n ${NAMESPACE} svc/api 8000:8000"
  echo "Login email: dev@gobifrost.com"

  if [[ -f "${KUBECONFIG_PATH}" ]]; then
    kubectl_local get pods,svc,job,scaledobject,hpa -n "${NAMESPACE}" || true
  fi
}

collect() {
  if [[ ! -f "${KUBECONFIG_PATH}" ]]; then
    echo "No local kubeconfig found at ${KUBECONFIG_PATH}" >&2
    exit 1
  fi

  local out_dir="${STATE_DIR}/collect/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "${out_dir}"
  kubectl_local get all,scaledobject,hpa -n "${NAMESPACE}" -o wide > "${out_dir}/resources.txt" || true
  kubectl_local describe pods -n "${NAMESPACE}" > "${out_dir}/pods-describe.txt" || true
  kubectl_local describe scaledobject -n "${NAMESPACE}" > "${out_dir}/scaledobjects-describe.txt" || true
  kubectl_local logs -n "${NAMESPACE}" -l app.kubernetes.io/name=bifrost-api --all-containers --tail=500 > "${out_dir}/api.log" || true
  kubectl_local logs -n "${NAMESPACE}" -l app.kubernetes.io/name=bifrost-scheduler --all-containers --tail=500 > "${out_dir}/scheduler.log" || true
  kubectl_local logs -n "${NAMESPACE}" -l app.kubernetes.io/name=bifrost-worker --all-containers --tail=500 > "${out_dir}/worker.log" || true
  kubectl_local logs -n "${NAMESPACE}" -l app.kubernetes.io/name=rabbitmq --all-containers --tail=500 > "${out_dir}/rabbitmq.log" || true
  echo "Collected diagnostics in ${out_dir}"
}

down() {
  download_kind
  if "${KIND_BIN}" get clusters | grep -Fxq "${CLUSTER_NAME}"; then
    "${KIND_BIN}" delete cluster --name "${CLUSTER_NAME}"
  fi
  rm -f "${KUBECONFIG_PATH}"
}

case "${1:-}" in
  experiment) shift; exec python3 "${ROOT_DIR}/scripts/kubernetes/spike.py" "$@" ;;
  build-experiment) shift; exec python3 "${ROOT_DIR}/scripts/kubernetes/build_spike.py" "$@" ;;
  up) up ;;
  status) status ;;
  down) down ;;
  collect) collect ;;
  kubectl)
    shift
    if [[ "${1:-}" == "--" ]]; then
      shift
    fi
    if [[ "$#" -eq 0 ]]; then
      usage >&2
      exit 1
    fi
    kubectl_local "$@"
    ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 1 ;;
esac
