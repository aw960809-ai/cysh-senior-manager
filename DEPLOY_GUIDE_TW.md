# 手動部署到 GitHub Pages（繁中）

## 1. 建立 Repository

1. 登入 GitHub。
2. 點右上角 `+` → `New repository`。
3. Repository name 建議：`cysh-senior-manager`。
4. 最簡單的方式是設成 `Public`。
5. 不要另外新增 README / .gitignore / License，直接建立空 repository。

## 2. 上傳本部署包「解壓後的內容」

Repository 根目錄最後必須長這樣：

```text
index.html
.nojekyll
README.md
DEPLOY_GUIDE_TW.md
data/
  opportunities.json
scripts/
  update_opportunities.py
  requirements.txt
.github/
  workflows/
    pages-and-opportunities.yml
```

注意：不要讓 GitHub 裡面多一層 `cysh_senior_manager_deploy/` 資料夾；`index.html` 必須直接位於 repository 根目錄。

## 3. 啟用 GitHub Pages

1. Repository → `Settings`。
2. 左側 → `Pages`。
3. `Build and deployment` → `Source` 選 **GitHub Actions**。
4. 回到 `Actions`。
5. 找到 `Deploy site and refresh opportunities`。
6. 第一次可按 `Run workflow` → `Run workflow`。

成功後，工作流程的 `deploy` job 會顯示網站網址。

## 4. 確認背景排程

進網站 → `系統` → `外部機會更新引擎`。

應逐步看到：

- 背景排程資料檔：已同步
- 網站關閉後背景排程：已部署
- 嘉義高中／教育部／青年發展署來源診斷

GitHub Actions 預設每天台灣時間約 08:15、20:15 執行。

## 5. 第一次正式使用前

1. 先在系統 → 備份／還原確認可匯出 JSON。
2. 再把試行假目標逐步換成該名學生的真實目標。
3. 個人資料只存在該裝置瀏覽器的 LocalStorage；清除瀏覽器網站資料前務必先「匯出備份」。

## 手機上傳提醒

GitHub 手機網頁對「整個資料夾」上傳較不方便。若手機檔案選擇器無法保留資料夾結構，建議：

- 使用電腦把解壓後整個資料夾拖進 GitHub 的 `Upload files`；或
- 在 GitHub 網頁用 `Add file → Create new file`，檔名直接輸入完整路徑（例如 `.github/workflows/pages-and-opportunities.yml`），再貼入對應檔案內容。

只要最終 repository 結構與上方一致即可。
