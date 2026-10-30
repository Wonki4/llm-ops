# 배포에 레시피 다시 적용 + 무중단 롤아웃 설정

## 배경

서빙 레시피로 배포를 올린 뒤 레시피가 잘못된 것을 알게 되면, 지금은 배포를 지우고
다시 올리는 수밖에 없다. 백엔드 `PUT /api/model-deployments/{id}`는 거의 모든 필드를
받아 매니페스트를 다시 적용하지만 UI에는 수정 경로가 없고, 레시피를 고쳐도 배포에는
아무 일도 일어나지 않는다 (`recipe_id`는 링크일 뿐이다).

사용자 요구 조건: **서비스가 중단되면 안 된다.**

## 목표

1. 배포 상세 페이지에서 "레시피에서 다시 적용"을 눌러 레시피의 서빙 설정을 배포에
   덮어쓰고 롤링 업데이트로 재적용한다. 적용 전에 바뀌는 항목과 롤아웃에 추가로
   필요한 GPU 수를 보여주고 확인받는다.
2. 롤아웃 전략을 매니페스트에 명시한다 (K8s 기본 25%/25%에 맡기지 않는다).
3. 옛 파드를 내릴 때 진행 중인 요청이 끊기지 않도록 정상 종료 설정을 기본으로 넣는다.

## 비목표

- 배포 설정을 자유롭게 편집하는 화면 (레시피를 고치고 다시 적용하는 흐름으로 대체).
- 레시피 쪽에서 "이 레시피를 쓰는 배포 N개에 일괄 적용".
- 멈춘 롤아웃(새 파드 Pending) 표시와 이전 설정으로 되돌리기 (후속 작업).
- Recreate 전략 (중단이 생기므로 제외).

## 무중단 롤아웃의 조건

| 상황 | 무중단 | 방법 |
|---|---|---|
| 여유 GPU가 1 replica만큼 있음 | 가능, 용량 저하 없음 | `surge`: maxSurge=1, maxUnavailable=0 (기본) |
| 여유 GPU 없음, replicas ≥ 2 | 가능, 롤아웃 동안 1 replica만큼 용량 감소 | `replace`: maxSurge=0, maxUnavailable=1 |
| 여유 GPU 없음, replicas = 1 | 불가능 | 포털이 막는다 (`replace` + replicas 1 → 400) |

maxSurge는 항상 1이다. 한 번에 한 파드씩 교체해 추가 GPU 요구를 최소로 유지한다.

## 결정 사항

### 롤아웃·종료 설정은 `runtime` JSONB 안에 둔다

레시피와 배포 둘 다 `runtime`을 가지며 P/D 풀은 `pool_row`로 상속받는다. 마이그레이션이
필요 없고 레시피 폼의 "런타임" 섹션에 자연스럽게 붙는다.

```json
{
  "rollout": "surge" | "replace",          // 생략 = surge
  "termination_grace_seconds": 120,        // 생략 = 120, 1..3600
  "prestop_sleep_seconds": 10              // 생략 = 10, 0..600 (0 = preStop 없음)
}
```

렌더링 (aggregated Deployment와 P/D 각 풀 Deployment 모두):

- `spec.strategy`: `{"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}`
  (`replace`면 0/1)
- `spec.template.spec.terminationGracePeriodSeconds`: 120
- 서빙 컨테이너 `lifecycle.preStop.exec.command`: `["sh", "-c", "sleep 10"]`
  (Service 엔드포인트 제거와 SIGTERM 사이의 시간차를 흡수. K8s 1.30+의 `sleep` 액션
  대신 exec을 써서 구버전 클러스터에서도 동작)

기본값은 매니페스트에서 채우므로 기존 배포도 다음 적용 때 같은 설정을 받는다.
벤치마크용 임시 서빙(`build_ephemeral_deployment`)도 같은 빌더를 쓰므로 같은 설정을
받는다. 해가 없다.

### `replace` + replicas 1은 막는다

생성과 수정(재적용 포함) 시 `check_rollout(dep)`: aggregated는 `replicas`, P/D는 풀마다
`role_view(...).replicas`를 보고 `replace`인데 정확히 1이면 400. 0은 "정지"이므로 통과.

### 레시피가 소유하는 필드

재적용은 아래 필드만 레시피 값으로 덮어쓴다.

```
image, model_path, engine, engine_args, vllm_extra_args, env,
gpu_count, gpu_resource_key, cpu_request, cpu_limit, memory_request, memory_limit,
node_selector, tolerations, storages, probes, runtime, pd_config
```

인스턴스가 소유하므로 유지: `model_name`, `litellm_model_name`, `cluster_id`, `namespace`,
`replicas`, `ingress_host/path/class`, `gpu_type`, `router_stack_*`, `serving_mode`.

- `pd_config`는 레시피 값을 쓰되 `prefill.replicas` / `decode.replicas`는 배포의 현재
  값을 유지한다 (배포 다이얼로그가 생성 때 하는 것과 같다).
- `gpu_type`은 배포 시점에 고른 값이므로 유지하고, 레시피의 `node_selector`/`tolerations`를
  덮어쓴 뒤 그 `gpu_type`의 프로필을 다시 적용한다 (`apply_profile`, P/D는 풀별).
  그렇지 않으면 프로필이 넣어준 노드 라벨이 사라진다.
- `serving_mode`가 다른 레시피는 적용할 수 없다 (409). aggregated ↔ P/D 전환은 라우터
  생성/삭제를 동반하므로 재배포가 맞다.

### LiteLLM 등록 공백

LiteLLM은 Service(80)를 보므로 롤아웃 중 주소가 바뀌지 않고, 새 파드는 readiness를
통과해야 Service에 들어간다. 재적용은 모델 이름과 LiteLLM 이름을 건드리지 않는다.
다만 레시피가 `--served-model-name`이나 `--api-key`를 바꾸면 기존 PUT과 같이 등록을
해제했다가 Ready 때 다시 등록한다. 이 경우 diff 응답에 `reregister: true`를 넣어
다이얼로그가 경고한다.

### API

- `GET /api/model-deployments/{id}/recipe-diff?recipe_id=` (super user)
  `recipe_id` 생략 = 배포의 `recipe_id`. 둘 다 없으면 404. serving_mode 불일치 409.
  ```json
  {
    "recipe": {"id": "…", "name": "…"},
    "changes": [{"field": "image", "current": "a", "proposed": "b"}],
    "rollout": {"mode": "surge", "extra_gpus": 8, "gpu_resource_key": "nvidia.com/gpu", "blocked": null},
    "reregister": false
  }
  ```
  `changes`는 정규화 후 값이 다른 필드만 (storages는 `{storage_id, mount_path, read_only}`로
  투영해 비교, `None`과 빈 dict/list는 같다고 본다). `rollout.blocked`는 `check_rollout`의
  메시지 (없으면 null). `extra_gpus`는 적용 후 설정 기준 (surge인 풀의 GPU 수 합).
- `POST /api/model-deployments/{id}/reapply` body `{"recipe_id": str | null}`
  diff와 같은 검증 → 레시피 값을 덮어쓰고 기존 PUT과 같은 경로로 K8s 재적용
  (`_apply_update`). 배포의 `recipe_id`를 적용한 레시피로 맞추고, 이벤트
  `RecipeApplied`(info)를 남긴다. 응답은 PUT과 같다 (직렬화된 배포 + warnings).

`update_deployment`의 본문은 `_apply_update(db, k8s, litellm, dep, updates, user)`로 뽑아
PUT과 reapply가 같이 쓴다.

### UI

- 배포 상세 페이지 헤더 버튼: "레시피에서 다시 적용" (레시피로 저장 / 삭제 옆).
- 다이얼로그: 레시피 선택(같은 serving_mode만, 배포의 레시피가 기본 선택), 변경
  항목 표(필드 / 현재 / 적용 후), 롤아웃 안내 (`surge`: "롤아웃 동안 GPU N개가 추가로
  필요합니다", `replace`: "롤아웃 동안 용량이 1 replica 줄어듭니다"), `reregister`면
  LiteLLM 재등록 경고, `blocked`면 이유를 보여주고 적용 버튼 비활성화. 변경 항목이
  없어도 적용은 가능하다 (파드를 다시 굴리고 싶을 수 있다).
- 레시피 폼 "런타임" 섹션: 롤아웃 방식 선택(surge/replace), 종료 유예(초), preStop
  대기(초). 비우면 기본값. 요약 줄에 기본값이 아닐 때만 표시.
- 배포 상세 설정 카드: "롤아웃" 필드에 `surge · grace 120s · preStop 10s` 형태로 표시.

## 검증

- 백엔드: `tests/test_serving_rollout.py` (스키마·렌더링·가드), `tests/test_deployment_reapply.py`
  (diff 계산, 엔드포인트 404/409/400, 재적용이 레시피 필드만 덮어쓰고 replicas·이름을 유지,
  이벤트 기록, PUT 리팩터 회귀).
- 프런트: `tsc --noEmit`, `eslint`; 브라우저에서 다이얼로그와 런타임 섹션 확인.
- 기존 베이스라인 실패 21건(Postgres 필요)은 그대로다.

## 개정 2026-10-30 — 다이얼로그 대신 YAML 비교 페이지

긴 값(인자 줄, kv-events JSON)이 들어오면 다이얼로그가 옆으로 늘어나 버튼이 화면 밖으로
밀렸고, 필드 단위 표는 실제로 클러스터에 무엇이 바뀌는지 읽기 어려웠다. 바꾼 것:

- 헤더 버튼은 `/admin/deployments/{id}/reapply` 페이지로 이동한다. 다이얼로그는 없앴다.
  `?recipe=<id>`로 다른 레시피를 고른다 (없으면 배포의 레시피). 새로고침해도 유지된다.
- 페이지: 위에 레시피 선택 · 취소 · 적용, 그 아래 롤아웃 안내 · 재등록 경고 · 적용 불가
  사유, 그 아래 매니페스트별 탭 (일반: Deployment / Service / Ingress, P/D: prefill·decode
  Deployment와 Service). 탭에 바뀐 줄 수, 바뀐 게 없으면 "같음". 처음엔 바뀐 게 있는 첫 탭.
  탭 안은 왼쪽 현재 YAML, 오른쪽 적용 후 YAML을 나란히 두고, 같은 줄은 변경 앞뒤 3줄만
  남기고 접는다 (하나씩 또는 "같은 줄 모두 펼치기"). 적용 성공 시 배포 상세로 돌아간다.
- `recipe-diff` 응답에 `manifests: [{kind, name, changed, rows}]`를 더한다. `rows`는
  `{op: equal|replace|delete|insert, left: [줄번호, 텍스트]|null, right: …}`.
  - 양쪽 다 포털 행으로 `build_all`을 돌려 만든다 (클러스터에서 읽지 않는다 — 기본값·상태
    필드 잡음 없이 적용이 바꾸는 줄만 보이게). 그래서 `kubectl edit`으로 직접 바꾼 내용은
    보이지 않는다.
  - 적용 후 쪽은 `_apply_update`와 같이 만든다: P/D 설정 정규화, 레시피 스토리지를
    배포의 볼륨 이름을 유지하며 해석, GPU 프로필 반영. 소유자 라벨은 양쪽 같은 기본값.
  - 적용 후 쪽을 만들 수 없으면 (해석 안 되는 스토리지, 포털이 관리하는 플래그)
    `manifests`는 빈 목록, 이유는 `rollout.blocked` — 재적용도 같은 이유로 400이다.
  - 줄 비교는 백엔드 `difflib`(`services/manifest_diff.py`). YAML은 줄 폭 제한 없이
    덤프해 인자 하나가 한 줄로 바뀐다. 프론트에 diff 라이브러리를 더하지 않는다.
- P/D 라우터(llm-d 스택)는 이 매니페스트에 없어 비교에 나오지 않는다 (재적용도 라우터를
  건드리지 않는다).
