# 嘉中高三一年自我管理系統

單人版、以嘉義市嘉義高中高三學生為對象的一年試行系統。

## 部署內容

- `index.html`：完整前端系統。
- `data/opportunities.json`：背景活動／獎學金資料快取。
- `scripts/update_opportunities.py`：背景抓取、解析、去重、更新與汰除引擎。
- `scripts/requirements.txt`：背景更新 Python 相依套件。
- `.github/workflows/pages-and-opportunities.yml`：GitHub Pages 部署＋每日兩次背景更新。
- `.nojekyll`：停用 Jekyll 轉換，直接發布靜態網站。

## 背景更新時間

工作流程預設每天台灣時間約：

- 08:15
- 20:15

也可以在 GitHub 的 **Actions** 頁面手動按 `Run workflow` 立即更新與部署。

## 重要資料原則

個人的目標、完成紀錄、個人事件等資料存在使用者瀏覽器的 `LocalStorage`，不會提交到 GitHub repository。GitHub 只保存公開活動／獎助候選資料。
