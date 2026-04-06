# 翻訳チェッカー - AI三者検証システム

日本語・英語のPDFを3つのAI（ChatGPT・Claude・Gemini）で自動チェックし、
相互検証・最終統合レポートを生成するWebアプリです。

## 使い方

1. 各AIのAPIキーを設定欄に入力
2. 日本語PDF（原文）と英語PDF（翻訳文）をアップロード
3. 固有名詞の翻訳ルールを入力（毎回異なる部分）
4. 「翻訳チェック開始」をクリック

## 処理フロー

| フェーズ | 内容 |
|---------|------|
| Phase 1 | 3つのAIが同時並列で翻訳をチェック |
| Phase 2 | 各AIが他のAIの指摘を参考に相互検証 |
| Phase 3 | 全結果を統合して最終レポートを生成 |

## APIキーの取得

| AI | 取得先 |
|----|-------|
| ChatGPT | https://platform.openai.com/api-keys |
| Claude | https://console.anthropic.com/settings/keys |
| Gemini | https://aistudio.google.com/apikey |

---

## GitHub Pages でのデプロイ方法

### 1. リポジトリのセットアップ

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/あなたのユーザー名/translation-checker.git
git push -u origin main
```

### 2. GitHub Pages を有効化

リポジトリの **Settings** → **Pages** → Branch: `main` / `/ (root)` → **Save**

数分後に `https://あなたのユーザー名.github.io/translation-checker/translation-checker.html` で公開されます。

---

## Claude CORS問題の解決（GitHub Pages 公開時に必要）

ChatGPT・Gemini はブラウザから直接呼び出せますが、
**Claude (Anthropic) はCORSポリシーにより直接呼び出しができません。**

Cloudflare Workers を使った無料のプロキシをデプロイすることで解決できます。

### Cloudflare Worker のデプロイ手順

1. [Cloudflare](https://dash.cloudflare.com) にアクセスしてアカウントを作成（無料）
2. ダッシュボードで **Workers & Pages** → **Create** → **Create Worker** をクリック
3. `worker.js` の内容をエディタに貼り付けて **Deploy** をクリック
4. 発行されたURL（例: `https://claude-proxy.yourname.workers.dev`）をコピー

### HTMLにWorker URLを設定

`translation-checker.html` をエディタで開き、冒頭の設定箇所を変更：

```javascript
// 変更前
const CLOUD_PROXY_URL = '';

// 変更後（あなたのWorker URLに変更）
const CLOUD_PROXY_URL = 'https://claude-proxy.yourname.workers.dev';
```

変更後にGitHubへプッシュすれば、使用者全員がそのままClaudeを使えます。

### Cloudflare Worker 無料プランの制限

- 1日あたり **10万リクエスト** まで無料
- 翻訳チェック用途では十分な量です

---

## ローカルで使う場合（Claude プロキシ）

GitHub Pages を使わずローカルで動かす場合：

```bash
python claude-proxy.py
```

起動後、アプリの Claude 設定で「プロキシを使用」をオンにしてください。

---

## ファイル構成

```
translation-checker.html  # メインアプリ
worker.js                 # Cloudflare Worker（Claudeプロキシ）
claude-proxy.py           # ローカル用Pythonプロキシ
README.md                 # このファイル
```

## 注意事項

- APIキーはブラウザのlocalStorageにのみ保存されます（外部送信なし）
- Cloudflare WorkerはAPIキーを転送するだけで保存しません
- 各AIサービスの利用料はユーザー自身のAPIキーに課金されます
