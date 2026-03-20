#!/bin/bash
set -euo pipefail

REGISTRY="registry.example.com/llm-ops"
BACKEND_IMAGE="$REGISTRY/litellm-portal-backend"
FRONTEND_IMAGE="$REGISTRY/litellm-portal-frontend"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 환경별 태그 매핑
declare -A TAG_MAP=( [dev]=dev [stg]=stg [prd]=stable )
declare -A API_URL_MAP=(
  [dev]="https://portal-api-dev.example.com"
  [stg]="https://portal-api-stg.example.com"
  [prd]="https://portal-api.example.com"
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] <dev|stg|prd> [backend|frontend]

빌드 → 푸시 → 배포를 한번에 수행합니다.

Arguments:
  dev|stg|prd           배포 환경
  backend|frontend      (선택) 특정 서비스만 배포. 생략 시 둘 다 배포

Options:
  --build-only          빌드+푸시만 (kubectl apply 안 함)
  --apply-only          kubectl apply + rollout restart만 (빌드 안 함)
  --no-cache            docker build 시 --no-cache 사용
  -h, --help            이 도움말 출력
EOF
  exit 0
}

# 옵션 파싱
BUILD=true
APPLY=true
NO_CACHE=""
SERVICE="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only) APPLY=false; shift ;;
    --apply-only) BUILD=false; shift ;;
    --no-cache)   NO_CACHE="--no-cache"; shift ;;
    -h|--help)    usage ;;
    dev|stg|prd)  ENV="$1"; shift ;;
    backend|frontend) SERVICE="$1"; shift ;;
    *) echo "ERROR: 알 수 없는 인자: $1"; usage ;;
  esac
done

if [[ -z "${ENV:-}" ]]; then
  echo "ERROR: 환경(dev|stg|prd)을 지정하세요."
  usage
fi

TAG="${TAG_MAP[$ENV]}"
API_URL="${API_URL_MAP[$ENV]}"
NAMESPACE="llm-ops-$ENV"
OVERLAY="$PROJECT_ROOT/deploy/kustomize/overlays/$ENV"

echo "=========================================="
echo " 환경: $ENV (tag: $TAG)"
echo " 서비스: $SERVICE"
echo " 빌드: $BUILD / 배포: $APPLY"
echo "=========================================="

# --- 빌드 + 푸시 ---
if [[ "$BUILD" == true ]]; then
  PIDS=()

  if [[ "$SERVICE" == "all" || "$SERVICE" == "backend" ]]; then
    echo "[BUILD] backend → $BACKEND_IMAGE:$TAG"
    docker build $NO_CACHE -t "$BACKEND_IMAGE:$TAG" "$PROJECT_ROOT/backend" &
    PIDS+=($!)
  fi

  if [[ "$SERVICE" == "all" || "$SERVICE" == "frontend" ]]; then
    echo "[BUILD] frontend → $FRONTEND_IMAGE:$TAG"
    docker build $NO_CACHE \
      --build-arg NEXT_PUBLIC_API_URL="$API_URL" \
      -t "$FRONTEND_IMAGE:$TAG" "$PROJECT_ROOT/frontend" &
    PIDS+=($!)
  fi

  # 빌드 완료 대기
  for pid in "${PIDS[@]}"; do
    if ! wait "$pid"; then
      echo "ERROR: 빌드 실패 (PID $pid)"
      exit 1
    fi
  done
  echo "[BUILD] 완료"

  # 푸시
  PIDS=()
  if [[ "$SERVICE" == "all" || "$SERVICE" == "backend" ]]; then
    echo "[PUSH] $BACKEND_IMAGE:$TAG"
    docker push "$BACKEND_IMAGE:$TAG" &
    PIDS+=($!)
  fi

  if [[ "$SERVICE" == "all" || "$SERVICE" == "frontend" ]]; then
    echo "[PUSH] $FRONTEND_IMAGE:$TAG"
    docker push "$FRONTEND_IMAGE:$TAG" &
    PIDS+=($!)
  fi

  for pid in "${PIDS[@]}"; do
    if ! wait "$pid"; then
      echo "ERROR: 푸시 실패 (PID $pid)"
      exit 1
    fi
  done
  echo "[PUSH] 완료"
fi

# --- 배포 ---
if [[ "$APPLY" == true ]]; then
  echo "[APPLY] kubectl apply -k $OVERLAY"
  kubectl apply -k "$OVERLAY"

  DEPLOYMENTS=()
  if [[ "$SERVICE" == "all" || "$SERVICE" == "backend" ]]; then
    DEPLOYMENTS+=("${ENV}-backend")
  fi
  if [[ "$SERVICE" == "all" || "$SERVICE" == "frontend" ]]; then
    DEPLOYMENTS+=("${ENV}-frontend")
  fi

  echo "[ROLLOUT] restart: ${DEPLOYMENTS[*]}"
  kubectl -n "$NAMESPACE" rollout restart deployment "${DEPLOYMENTS[@]}"

  # 롤아웃 상태 확인
  for dep in "${DEPLOYMENTS[@]}"; do
    echo "[ROLLOUT] $dep 배포 대기중..."
    kubectl -n "$NAMESPACE" rollout status deployment "$dep" --timeout=120s
  done
  echo "[DEPLOY] 완료"
fi

echo "=========================================="
echo " 배포 성공: $ENV ($SERVICE)"
echo "=========================================="
