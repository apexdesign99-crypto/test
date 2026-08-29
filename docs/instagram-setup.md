# Instagram 連携の手順

事務所の Instagram アカウントから、投稿の実績(views・リーチ・保存など)を
取り込むための設定手順です。

> **この手順書について**
>
> Meta の開発者コンソールは画面構成も名称も頻繁に変わります。**ここに書いた
> ボタン名や画面の位置は、実際と違っている可能性があります。** 迷ったら
> [Instagram Platform 公式ドキュメント](https://developers.facebook.com/docs/instagram-platform/)
> を一次情報として確認してください。
>
> API の仕様(取得できる指標など)は 2026 年 8 月時点で確認したものです。

---

## 前提

- Instagram アカウントが**プロフェッショナルアカウント**(ビジネスまたはクリエイター)であること
  - 個人アカウントでは API を使えません。Instagram アプリの設定から切り替えられます
- Facebook ページの連携は**不要**です
  - 「Instagram API with Instagram Login」という経路を使うため
- **App Review(アプリ審査)は不要**です
  - 自社アカウントだけを見る場合、開発者自身のアカウントに対する
    「Standard Access」で足ります。他社のアカウントを扱う場合のみ審査が要ります

所要時間はおおむね 30〜60 分です。

---

## 手順 1. Meta の開発者アカウントを作る

1. [developers.facebook.com](https://developers.facebook.com/) にアクセスします
2. 事務所の Facebook アカウントでログインし、開発者登録をします
3. 電話番号などの本人確認を求められた場合は済ませます

## 手順 2. アプリを作成する

1. 開発者コンソールで新しいアプリを作成します
2. ユースケースを選ぶ画面では、**Instagram に関するもの**を選びます
   - 「Instagram の API を使ってビジネスアカウントを管理する」といった趣旨の項目です
3. アプリ名は社内で分かるもの(例:`アペックス設計 社内連携`)にします
   - **アプリ名に「Instagram」「Insta」「IG」は使えません**。Meta のブランド規約で弾かれます

## 手順 3. Instagram のプロダクトを追加し、アカウントを繋ぐ

1. アプリの管理画面で Instagram のプロダクトを追加します
2. **「Instagram API with Instagram Login」**(Facebook ログインではないほう)の設定に進みます
3. 事務所の Instagram プロフェッショナルアカウントを、アプリのテストユーザーとして追加します
4. Instagram アプリ側で連携の承認が必要な場合があります(通知を確認してください)

## 手順 4. アクセストークンを取得する

必要な権限(スコープ)は **`instagram_business_basic`** です。
投稿と指標の読み取りにはこれで足ります。

コンソールにトークン生成のツールがあれば、そこから取得するのが最短です。
自社アカウント用の**長期トークン(有効期間 60 日)**を取得してください。

> 短期トークン(1時間)しか出ない場合は、長期トークンへの交換が必要です。
> 交換手順は
> [Access Token のドキュメント](https://developers.facebook.com/docs/instagram-platform/reference/access_token/)
> を参照してください。

## 手順 5. このツールに登録する

```bash
python -m ai_employee instagram --connect "取得したトークン"
```

- トークンの有効性を確認してから保存します(使えないトークンは保存しません)
- 保存先は `ai-office/_company/instagram_credentials.json` で、
  **所有者だけが読めるパーミッション(600)**にします
- **トークンは画面にもログにも出しません**

環境変数 `INSTAGRAM_ACCESS_TOKEN` で渡すこともできます。

## 手順 6. 取り込む

```bash
python -m ai_employee instagram --sync
python -m ai_employee instagram              # 状態と実績の確認
```

ブラウザの「発信」タブでも実績を見られます。

---

## 運用上の注意

### トークンは 60 日で切れます

```bash
python -m ai_employee instagram --refresh
```

- 更新すると、そこからさらに 60 日有効になります
- **更新できるのは、期限内かつ発行から 24 時間以上経ったトークンだけ**です
- **期限が切れたトークンは更新できません。**手順 4 から取り直しになります
- 残り 14 日を切ると、`instagram` コマンドが警告を出します

月初の運用点検のときに、あわせて `--refresh` を実行する運用にすると切らさずに済みます。

### 取得できる指標

| 指標 | 意味 |
| --- | --- |
| `views` | 表示された回数。同じ人の再表示も数える |
| `reach` | 見たアカウント数。同じ人は 1 回だけ |
| `saved` | 保存された数 |
| `shares` | シェアされた数 |
| `total_interactions` | いいね・コメント・保存・シェアの合計 |

**2025 年に `impressions` と `video_views` は廃止されました。** 現在は `views` が
すべての形式(フィード・カルーセル・リール)の共通指標です。古い記事やツールが
`impressions` を使っていることがありますが、要求するとエラーになります。

`profile_views`・`website_clicks`・`email_contacts` なども廃止済みです。

### 投稿の公開(任意・追加設定が必要)

読み取りだけなら、ここは飛ばして構いません。

#### 追加で必要なもの

- 権限に **`instagram_business_content_publish`** を追加し、トークンを取り直す
- **画像を公開された HTTPS の URL に置くこと**

2つめが実務上の壁です。**Instagram のサーバが画像を取りに来る**ので、
手元の PC にある画像はそのままでは投稿できません。事務所の HP など、
外部から見える場所にアップロードした URL が必要です。

| 画像の要件 | |
| --- | --- |
| 形式 | JPEG |
| 縦横比 | 4:5 〜 1.91:1 |
| 幅 | 320〜1440px |
| ファイルサイズ | 8MB 以下 |
| URL | 公開された HTTPS の直接リンク(HTML ページやリダイレクト、Google ドライブの共有リンクは不可) |

#### 使い方

```bash
# 1. まず下見(何も投稿されません)
python -m ai_employee publish <計画のID> --image-url "https://example.com/post.jpg"

# 2. 内容を確認してから公開
python -m ai_employee publish <計画のID> --image-url "https://example.com/post.jpg" --confirm
```

下見では、投稿される本文・画像・確認事項が表示されます。

```
下見です。まだ何も投稿していません。

  計画    : [cc516c1d] 施工事例カルーセル  予定日 2026-09-25
  本文    : 44 文字
    K様邸の中庭。地域No.1の設計事務所として、必ず…

  確認してください(4 件)
    ・計画上、素材が未確認のままです。
    ・掲載条件があります: 施主名は伏せる。外観写真のみ。
    ・[最上級表現] No.1 — 客観的な裏付けと出典がなければ使えない。
    ・[断定・保証] 必ず — 結果を保証する表現。
```

#### 安全のための決めごと

**公開は取り消せません。**削除しても、見た人には既に届いています。
そのため次のようにしています。

1. **AI社員はこの操作を行えません。** 公開ツールを社員に渡していないので、
   社員が独断で投稿することはありません。原稿と計画を用意するところまでが社員の仕事で、
   実行は人が行います
2. **既定は下見。** `--confirm` を付けない限り、API を一度も呼びません
3. **次の2つは `--confirm` でも通りません**
   - 題材の案件に掲載許諾がない
   - その投稿が既に「投稿済」(二重投稿の防止)
4. **掲載条件と表現の指摘は下見に出ます。** 判断は人が行います

公開に成功すると、投稿計画が「投稿済」になり、案件の発信履歴にも記録されます。

> Instagram の制限で、API 経由の公開は **24時間あたり100件**までです。
> 通常の運用で当たることはありません。

### 取得できないもの

- **ストーリーズの指標** — この経路では取得できません
  (Facebook ログイン経由の設定が必要です)
- **ストーリーズへの投稿** — この経路では公開できません

### 指標が取れない投稿があります

投稿の種類や作成時期によって、一部の指標が返らないことがあります。
そのときは **0 ではなく「—」(欠測)として記録します**。
「反応がなかった」と「取得できなかった」は別のことなので、混ぜません。

---

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| `(190)` 系のエラー | トークンが無効か期限切れ。手順 4 から取り直し |
| `(100) metric not available` | 廃止済みの指標を要求している、または投稿の種類が対応していない |
| `(4)` `(17)` 系のエラー | レート制限。時間をおいて再実行 |
| 公開時に「画像を取得できませんでした」 | 画像の URL が公開されているか、JPEG か、サイズが要件内かを確認 |
| アカウント情報が取れない | プロフェッショナルアカウントになっているか確認 |
| そもそも接続できない | 事務所のネットワークが `graph.instagram.com` を遮断していないか |

エラーメッセージは Meta から返ってきたものをそのまま表示します。
検索するときはメッセージ全文で調べると見つかりやすいです。

---

## 参考(一次情報)

- [Instagram Platform 概要](https://developers.facebook.com/docs/instagram-platform/)
- [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)
- [Insights](https://developers.facebook.com/docs/instagram-platform/insights/)
- [Access Token](https://developers.facebook.com/docs/instagram-platform/reference/access_token/)
- [Refresh Access Token](https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token/)
- [Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
