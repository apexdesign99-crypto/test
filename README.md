# AI社員 — 建築設計事務所向け

集客・営業・マーケティング・事務・BIM設計の 5 職種を AI社員として立ち上げ、
**事務所全体で 1 つの案件台帳を共有**しながら働かせるためのツールです。

集客が拾った反響を営業が追い、設計が図面を起こし、事務が請求する——という流れは、
全員が同じ案件を見られないと成立しません。この台帳が中心にあります。

思考エンジンは Claude Opus 5(`claude-opus-5`)です。

## クイックスタート

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...   # または `ant auth login`

# 1. 標準陣容(集客・営業・マーケ・事務・BIM)を一括採用
python -m ai_employee hire-team

# 2. 反響が入ったら集客担当に渡す
python -m ai_employee ask --id shukyaku \
  "HP から問い合わせ。田中様、世田谷区で戸建の新築を検討中。案件を起こして初回返信を作って"

# 3. 営業担当が引き継ぐ(同じ案件台帳を見ている)
python -m ai_employee ask --id eigyo "田中様の初回相談、ヒアリングの抜けを洗い出して"

# 4. 事務所の全案件を確認
python -m ai_employee projects
python -m ai_employee projects --pipeline
```

## 5 職種

| ID | 役職 | 担当 |
| --- | --- | --- |
| `shukyaku` | 集客担当 | 反響の一次整理と案件化、流入経路の記録、初回返信の下書き、追客漏れの検知 |
| `eigyo` | 営業担当 | ヒアリング整理と未確認事項の洗い出し、プラン・概算見積の下書き、設計契約までの追客 |
| `marke` | マーケティング担当 | 事例記事・SNS の下書き、発信ネタの棚卸し、競合・市場調査(Web 検索が有効) |
| `jimu` | 事務担当 | 契約書・見積書の記載チェック、請求と入金予定の管理、提出書類のチェックリスト |
| `bim` | BIM設計担当 | モデリング方針と命名規則、図面セットの整合チェック、面積・数量の拾い、設計変更の履歴管理 |

個別に採用することもできます。

```bash
python -m ai_employee hire --id eigyo2 --name "営業 AI 二号" --template sales
python -m ai_employee templates    # テンプレート一覧
```

採用後の職務定義書は `ai-office/<社員ID>/profile.json` に置かれます。
担当業務・行動指針・口調・ツール権限はこのファイルを直接編集して、事務所の実態に合わせてください。

## 案件台帳

事務所で 1 つだけ持つ共有台帳です(`ai-office/_company/projects.json`)。
全社員がここを読み書きするので、部署をまたいだ引き継ぎが記録として残ります。

**ステージ**: 反響 → 初回相談 → 現地調査 → プラン提案 → 見積 → 設計契約 →
基本設計 → 実施設計 → 確認申請 → 着工 → 監理 → 竣工 → アフター

**ステータス**: `active` 進行中 / `won` 受注 / `lost` 失注 / `onhold` 保留 / `done` 完了

案件の更新には必ず理由(`note`)が要ります。誰がいつ何をなぜ変えたかが履歴に差分つきで残るため、
「この案件、前回どこまで話したか」を後から追えます。

```bash
python -m ai_employee projects --stage 実施設計   # ステージで絞る
python -m ai_employee projects --owner bim        # 担当で絞る
python -m ai_employee projects --query 世田谷     # 案件名・顧客名・計画地で検索
python -m ai_employee project <案件ID>            # 1 件の詳細と全経緯
```

ステージ・用途種別は `ai_employee/company.py` の `STAGES` / `KINDS` を編集すれば変えられます。

## 社員が使えるツール

| ツール | 用途 |
| --- | --- |
| `add_project` / `list_projects` / `get_project` | 案件の起票・検索・経緯の確認 |
| `update_project` / `log_project` | 案件の更新と履歴追記(理由の記載が必須) |
| `pipeline` | ステージ別の進行中件数(営業会議の材料) |
| `record_note` / `search_notes` | 業務メモ。`project_id` で案件に紐付く |
| `add_task` / `list_tasks` / `complete_task` | 自分のタスク管理 |
| `list_files` / `read_file` / `write_file` | 成果物の読み書き |
| `current_datetime` | 現在日時・曜日 |
| `web_search` | Web 検索(マーケ担当と `--web` 付きで採用した社員のみ) |

`profile.json` の `tools` に列挙された名前だけが有効です。書かれていないツールはモデルに提示すら
されないため、権限の付与・剥奪はこのファイルで完結します。

ファイル操作は `ai-office/<社員ID>/files/` の中に閉じ込められています。
`..` やシンボリックリンクによるワークスペース外へのアクセスは拒否されます。

## この AI社員がやらないこと

事務所の業務では、AI が知ったかぶりをすると実害が出ます。各職種の行動指針で以下を禁じています。

- **法規を記憶で断定しない**(BIM設計・事務)。建築基準法・条例・省エネ基準の適合判断や
  提出書類の要否は、法令原文と所管行政庁で確認するよう指示し、判断が要る箇所は「要確認」と
  明示させます。
- **モデルを見ずに図面を推測しない**(BIM設計)。Revit / Archicad を直接操作できないことを
  自認させ、指示は担当者がそのまま実行できる手順として書かせます。
- **未確認の数字を書かない**(営業・事務)。予算・面積・工期・金額は「聞けた事実」と「未確認」を
  必ず区別させ、概算には前提と未確定である旨を併記させます。
- **施主の個人情報を無断で原稿に載せない**(マーケ)。掲載許諾の確認を必ず論点として挙げ、
  「業界No.1」のような裏付けのない優良誤認表現を禁じています。

これらはプロンプト上の制約であり、出力の確認を不要にするものではありません。
対外的な文書と法規判断は、必ず人が確認してから使ってください。

## ワークスペースの中身

```
ai-office/
├── _company/
│   └── projects.json    案件台帳(事務所で共有)
└── <社員ID>/
    ├── profile.json     職務定義書(人間が編集する)
    ├── notes.jsonl      業務メモ(追記のみ)
    ├── tasks.json       タスク一覧
    ├── files/           成果物(社員が読み書きできる唯一の領域)
    └── sessions/        日付ごとの会話ログ
```

置き場所は `--office` または環境変数 `AI_EMPLOYEE_HOME` で変更できます。
案件台帳は読み込み→更新→書き戻しの単純な方式なので、複数の社員を同時並行で走らせる場合は
同じ案件への同時更新を避けてください。

## コマンド一覧

| コマンド | 説明 |
| --- | --- |
| `hire-team` | 標準陣容 5 名を一括採用する |
| `hire` | 個別に採用する |
| `roster` | 在籍者と未完了タスク数を一覧する |
| `ask` | 単発で業務を依頼する(`--remember` で当日の会話に引き継ぐ) |
| `chat` | 対話しながら業務を進める(`/exit` で終了、`/clear` で履歴消去) |
| `report` | 当日の記録から日報を書かせて `files/reports/` に保存する |
| `projects` / `project` | 案件台帳の一覧・詳細(`--pipeline` でステージ別件数) |
| `tasks` / `notes` | 社員のタスク・メモを人間が確認する |
| `templates` | 職種テンプレートを一覧する |

`--thinking` を付けると、社員の思考の要約もストリーミング表示されます。

## Python から使う

```python
from ai_employee import Employee, Workspace

ws = Workspace("eigyo")
employee = Employee(ws.load_profile(), ws)

result = employee.work("今週フォローすべき案件を、優先順位をつけて出して")
print(result.text)
print(result.tool_calls)   # 実際に使ったツール
print(result.ok)           # 拒否・打ち切りがなければ True

# 案件台帳には直接アクセスもできる
for pj in employee.ledger.list(stage="実施設計"):
    print(pj["name"], pj["next_action"])
```

`Listener` を継承して `Employee(..., listener=...)` に渡すと、
本文・思考・ツール呼び出しをストリーミングで受け取れます(CLI の画面表示もこれを使っています)。

## 設計上の判断

- **エージェントループは手書き**。ツール実行の途中経過をトークン単位で表示したいこと、
  ベータ依存(Tool Runner)を避けたいことから、`client.beta.messages.stream` を使った
  手動ループにしています。並列ツール呼び出しの結果は 1 つの user メッセージにまとめて返します。
- **プロンプトキャッシュ**。system は「職務定義書(不変)→ 現在時刻・未完了タスク・担当案件(揮発)」の
  順に組み立て、不変部分の末尾だけにキャッシュ区切りを置いています。案件が動いても
  職務定義書側のキャッシュは効き続けます。ツールの並び順も指定順に依存しないよう定義順に固定。
- **拒否時のフォールバック**。Opus 5 は安全性判定で `stop_reason: "refusal"` を返すことがあるため、
  サーバ側フォールバック(`fallbacks: "default"`)を既定で有効にしています。
  それでも拒否された場合は例外にせず `TurnResult.refusal` として返します。
- **暴走の抑制**。ツール呼び出しは 1 依頼あたり 24 回、`pause_turn` からの再開は 5 回で打ち切ります。
- **社員は落ちない**。ツールの失敗は例外にせず `is_error` 付きの結果としてモデルに返し、
  社員自身に回復させます。

## テスト

```bash
pip install -e ".[dev]"
python -m pytest
```

API は呼びません。台本どおりに応答するダミークライアントで、
ツール実行・並列呼び出し・拒否・出力上限・`pause_turn` 再開・上限打ち切り・
案件台帳の共有までを検証しています。
