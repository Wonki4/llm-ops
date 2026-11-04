# 모델 접근 그룹 관리 + 그룹 단위 개인 키 권한 — Design

Date: 2026-11-04
Status: implemented on `feat/model-access-groups`
Related: `2026-10-07-personal-keys-user-model-grants-design.md` (개인 키와 사용자 행 `models`), LiteLLM v1.102.0 `proxy/management_endpoints/model_access_group_management_endpoints.py`

## 왜

모델 접근 그룹(`model_info.access_groups`)에 사용자를 넣으려면 지금은 사용자 상세의 "개인 키 권한"에서
한 명씩 고쳐야 한다(20명 = 20번). 그룹이나 모델 기준으로 누가 권한을 가졌는지도 볼 곳이 없다.

## 결정

- **권한의 원본은 그대로** LiteLLM 사용자 행 `models`. 그룹 화면은 `models`에 그룹 이름이 든 사용자를
  모아 보여주고, 넣고 빼는 것도 그 목록을 고친다. 사용자 상세의 개인 키 권한 편집과 같은 값이라 두 화면이 어긋나지 않는다.
  그룹 권한은 지금처럼 **개인 키(팀 없는 키)에만** 적용된다(LiteLLM은 팀 키에 사용자 `models`를 보지 않는다).
- **그룹 편집은 LiteLLM `/access_group/*`** (`new` / `{name}/update` / `{name}/delete`). 이 API는 DB 모델만
  건드린다. config.yaml 배포가 가진 그룹 멤버십은 **잠김**으로 표시하고 뺄 수 없다. 잠긴 멤버가 있는 그룹은 포털에서 삭제할 수 없다.
  - LiteLLM은 DB 배포에 붙은 그룹만 "있는 그룹"으로 본다. config 모델만 가진 그룹에 DB 모델을 넣을 때는 `new`로 간다.
  - LiteLLM `update`는 빈 목록을 받지 않는다. 모델을 다 빼는 것은 "그룹 삭제"로만 한다(잠긴 멤버만 남는 경우는 DB 쪽 태그만 지운다).
- **그룹 삭제는 사용자 권한에서도 그룹 이름을 지운다.** 남겨 두면 같은 이름으로 그룹을 다시 만들 때 옛 권한이 되살아난다.
  남는 권한이 없으면 `["no-default-models"]`(포털은 `[]`를 쓰지 않는다).
- **그룹 이름**: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, LiteLLM 센티널(`all-proxy-models`, `no-default-models`)과
  모델 이름은 금지(권한 목록에서 모델인지 그룹인지 모호해진다).
- **사용자 추가**: 기본은 모달에서 검색해 한 명씩 담기, 보조로 "명단 붙여넣기"(줄바꿈·쉼표·공백 구분). 담은 사람은 한 번에 저장한다.
  붙여넣기는 저장 전에 서버 dry-run으로 담김 / 이미 그룹에 있음 / ID 형식 아님을 나눠 보여준다.
  ID는 대문자로 정규화(`^[A-Z0-9][A-Z0-9._@-]{0,127}$`, 포털 사용자 ID 규칙).
- **로그인 전 사용자도 미리 권한을 받는다.** LiteLLM 행이 없으면 `/user/new`로 만들고 `metadata.portal_pregrant: true`를 남긴다.
  첫 로그인(`auto_provision_user`)은 원래 행이 있으면 아무것도 안 했는데, 이 표시가 있으면 기본 팀 배정과 이메일을 한 번 채우고 표시를 지운다.
- **"전체 권한" 사용자**에게 그룹을 넣으면 그룹 이름을 함께 남긴다(전체 권한을 거둬도 그룹 권한은 남게). 목록에 "전체 권한" 배지로 표시한다.
- **모델 관점**: 모델 상세 페이지(슈퍼유저만)에 "개인 키로 쓸 수 있는 사용자" 카드 — 직접 부여 / 그룹 경유 / 전체 권한. 펼칠 때 조회한다.

## API (Super User)

| | |
|---|---|
| `GET /api/admin/access-groups` | 그룹(모델·잠김·사용자 수)과 모델(편집 가능 = DB 배포 있음, 속한 그룹) |
| `GET /api/admin/access-groups/{group}/users` | 그룹 권한을 가진 사용자(로그인 여부, 전체 권한 여부, 개인 키 수) |
| `GET /api/admin/access-groups/model-users?model=` | 개인 키로 모델을 쓸 수 있는 사용자와 경로(`direct`, `all`, 그룹 이름) |
| `POST /api/admin/access-groups` `{name, model_names}` | 그룹 생성(409 이미 있음, 400 이름·모델) |
| `PUT /api/admin/access-groups/{group}` `{model_names}` | 모델 목록 교체(잠긴 멤버는 목록에 남아야 함) |
| `DELETE /api/admin/access-groups/{group}` | 그룹 삭제 + 사용자 권한에서 회수(409 잠김) |
| `POST /api/admin/access-groups/{group}/users` `{user_ids, dry_run}` | 권한 부여; 결과 `added` / `pregranted` / `already` / `invalid` / `error` |
| `POST /api/admin/access-groups/{group}/users/remove` `{user_ids}` | 권한 회수; 결과 `removed` / `not_member` / `invalid` / `error` |

사용자별 쓰기는 `/user/update`(`models`)로 해서 LiteLLM의 사용자 캐시도 갱신된다. 한 사용자의 실패는 `error`로 돌려주고 나머지는 계속한다.

## 화면

관리자 메뉴 "모델 접근 그룹"(`/admin/access-groups`): 왼쪽 그룹 목록(모델 수·사용자 수, 잠김 표시) + 오른쪽 상세
(모델 배지와 "모델 편집", 사용자 목록·필터·"사용자 추가"·"빼기", "그룹 삭제").

## 범위 밖 / 알려진 한계

- 그룹 이름 변경(LiteLLM API에 없음), 그룹별 예산(LiteLLM `/access_group/{name}/budget`), 팀 키 권한.
- config.yaml 모델의 그룹 멤버십 변경.
- 같은 사용자를 두 관리자가 동시에 고치면 마지막 쓰기가 이긴다(읽고-고쳐-쓰기).
