# Keycloak OIDC kubeconfig로 클러스터 등록

관리자 **설정 → 클러스터**에서 `auth-provider.name: oidc`인 kubeconfig를 등록할 수 있습니다. `client-secret`이 없는 Keycloak 공개 클라이언트 형식도 지원합니다.

선택한 context의 user에는 다음 설정이 필요합니다.

```yaml
users:
  - name: keycloak-user
    user:
      auth-provider:
        name: oidc
        config:
          client-id: YOUR_CLIENT_ID
          idp-issuer-url: https://keycloak.example.com/realms/YOUR_REALM
          id-token: YOUR_ID_TOKEN
          refresh-token: YOUR_REFRESH_TOKEN
          idp-certificate-authority: /local/path/to/keycloak-ca.pem
```

`id-token` 또는 `refresh-token` 중 하나는 있어야 합니다. 만료가 가까운 ID 토큰은 저장된 클러스터에 접속할 때 Keycloak에서 갱신하며, 새 ID 토큰과 refresh token을 암호화된 kubeconfig에 다시 저장합니다. 갱신에는 kubeconfig의 `client-id`를 사용하고, `client-secret`이 있으면 함께 전송합니다.

`idp-certificate-authority`가 관리자 PC의 파일 경로라면 **OIDC CA 인증서** 입력에서 해당 PEM 파일을 업로드하세요. 포털이 인증서를 `idp-certificate-authority-data`로 kubeconfig에 내장합니다. 이미 `idp-certificate-authority-data`가 있거나 파일 경로가 백엔드 컨테이너에서도 유효하면 업로드할 필요가 없습니다. Kubernetes API 서버용 `clusters[].cluster.certificate-authority` 경로도 백엔드에서 접근할 수 없으면 `certificate-authority-data`로 바꿔야 합니다.

토큰이 만료된 kubeconfig는 먼저 저장한 다음 **저장된 클러스터의 연결 테스트**를 실행하세요. 저장 전 연결 테스트에서는 토큰을 갱신하고 저장할 곳이 없어 만료된 토큰을 사용할 수 없습니다. 백엔드가 `idp-issuer-url`의 Keycloak과 Kubernetes API 서버에 접근할 수 있어야 하며, Kubernetes API 서버는 같은 OIDC issuer와 client ID를 신뢰해야 합니다. Keycloak refresh token도 만료되거나 취소되면 새 kubeconfig를 등록해야 합니다.

이 설정은 **포털 → Kubernetes API** 인증에만 적용됩니다. 포털 사용자 로그인이나 관리자 MCP 로그인 설정과는 별개입니다.

참고: [Kubernetes OIDC 인증 문서](https://kubernetes.io/docs/reference/access-authn-authz/authentication/#openid-connect-tokens)
