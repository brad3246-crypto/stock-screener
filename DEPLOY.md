# 배포 가이드 — 항상 켜진 공개 사이트 (Streamlit Community Cloud)

내 PC와 무관하게 24시간 누구나 접속 가능한 무료 사이트로 올리는 절차.
**브라우저에서 본인이 직접** 해야 하는 단계(★)와, 터미널에서 하는 단계가 섞여 있다.

---

## 0. 사전: git/gh 설치 + GitHub 로그인 (1회)
```powershell
gh auth login        # 브라우저로 GitHub 로그인 (안내 따라 Enter)
```

## 1. 저장소 만들고 올리기 (1회)
이미 로컬에 git 커밋이 되어 있다면:
```powershell
cd C:\Users\brad3\stock-screener
gh repo create stock-screener --public --source . --push
```
→ `https://github.com/<내아이디>/stock-screener` 생성됨.

> ⚠️ DART 키가 든 `.streamlit/secrets.toml` 은 `.gitignore` 로 **올라가지 않는다**(정상).
> 데이터 캐시(`data/*.parquet`)는 배포에 필요하므로 **포함**된다.

## 2. ★ Streamlit Cloud 배포
1. https://share.streamlit.io 접속 → **Sign in with GitHub**
2. **Create app → Deploy a public app from GitHub**
3. 설정:
   - Repository: `<내아이디>/stock-screener`
   - Branch: `main`
   - Main file path: `app.py`
4. **Advanced settings → Secrets** 칸에 아래 입력:
   ```toml
   DART_API_KEY = "761d6e8b5f6c50cf28413e42a43bb24358ff2ea9"
   ```
5. **Deploy** 클릭 → 몇 분 뒤 `https://<앱이름>.streamlit.app` 공개 주소 발급.

## 3. ★ 자동 갱신용 GitHub 시크릿 (1회)
분기 자동 재수집(Actions)이 DART 키를 쓰도록:
1. GitHub 저장소 → **Settings → Secrets and variables → Actions**
2. **New repository secret**
   - Name: `DART_API_KEY`
   - Secret: `761d6e8b5f6c50cf28413e42a43bb24358ff2ea9`
3. 저장. 이후 3·5·8·11월 25일에 `.github/workflows/refetch.yml` 이 자동으로 재무를 갱신하고 커밋 → 사이트 자동 재배포.
   (Actions 탭에서 **Run workflow** 로 수동 실행도 가능)

---

## 데이터 갱신 경로 정리
- **주가·시총(POR)**: 사이트 접속 시 실시간(FDR). 막히면 동봉 캐시로 폴백.
- **재무(영업이익·ROE)**: GitHub Actions가 분기마다 자동 갱신. 급하면 로컬에서
  `python -m screener.fetch --force` 후 `git add -A && git commit -m update && git push`.

## 보안 메모
- DART 키가 공개 사이트에 들어가므로 **남들이 당신 키 사용량(일 2만 콜)을 공유**한다.
  개인/소수 공유는 무방하나, 트래픽이 커지면 키 분리나 접근 제한을 고려.
