# v10 Web Push 後端（Cloudflare Worker）

後端使用 Cloudflare Worker + SQLite Durable Object Alarm。每台裝置只有自己的匿名 Durable Object；它只保存 PushSubscription、提醒時間與摘要型文字，不保存完整目標、完成紀錄或 LocalStorage。

## 推薦部署方式：GitHub Actions

Termux 的 Android 環境不一定能直接執行 Wrangler 的原生 runtime，因此本專案附有 `.github/workflows/deploy-web-push-worker.yml`，由 GitHub 的 Ubuntu runner 幫你部署。

### 1. 先有 Cloudflare 帳號

建立 Cloudflare 帳號後，取得：
- Account ID
- API Token：使用 Cloudflare 的 `Edit Cloudflare Workers` 權限範本，並限制在自己的帳號。

### 2. 在 Termux 產生一次 VAPID 金鑰

只需要 Node.js，不需要 Wrangler：

```bash
pkg install nodejs -y
cd ~/cysh-senior-manager/push-worker
node generate-vapid.mjs
```

會顯示：
- `VAPID_SERVER_PUBLIC_KEY`
- `VAPID_SERVER_PRIVATE_KEY`

私鑰不可放進 repository。

### 3. 在 GitHub repository 建立四個 Actions secrets

到 `Settings → Secrets and variables → Actions` 新增：

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `VAPID_SERVER_PUBLIC_KEY`
- `VAPID_SERVER_PRIVATE_KEY`

### 4. 執行部署

到 `Actions → Deploy Web Push Worker → Run workflow`。

成功後 log 會顯示 Worker 網址，例如：

`https://cysh-senior-manager-push.<你的子網域>.workers.dev`

### 5. App 連接

正式網站／PWA：`系統 → 通知中心 → Cloudflare Worker 網址`，貼入 Worker URL，按：

1. `連接背景推播`
2. `同步未來提醒`
3. `測試背景推播`

測試成功後，即使 App 完全關閉，排定的 Web Push 仍可喚醒 Service Worker 顯示通知。

## 金鑰規則

VAPID 金鑰只產生一次。不要隨意更換私鑰；若更換，舊 PushSubscription 需要重新連接。
