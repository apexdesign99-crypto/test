# AI社員

職務定義書を 1 枚書くと、その通りに働く AI社員が立ち上がります。
指示を受け、自分でツールを使って調べ、業務メモとタスクを残し、成果物をファイルに保存します。
記録は残るので、翌日以降も経緯を引き継いで働けます。

思考エンジンは Claude Opus 5(`claude-opus-5`)です。

## クイックスタート

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...   # または `ant auth login`

# 1. 採用する
python -m ai_employee hire --id sato --name "佐藤 AI" --template sales

# 2. 仕事を頼む
python -m ai_employee ask --id sato "A社との商談内容を記録して、次のアクションをタスクにして"

# 3. 対話しながら進める
python -m ai_employee chat --id sato

# 4. 日報を書かせる
python -m ai_employee report --id sato
```

## 職種テンプレート

`--template` で職種を選ぶと、役職・担当業務・行動指針・権限が設定された状態で採用されます。

| テンプレート | 役職 | 担当 |
| --- | --- | --- |
| `assistant` | 業務アシスタント | 調査・整理・文書の下書き |
| `sales` | 営業アシスタント | 商談記録、提案・見積の下書き、フォロー管理 |
| `support` | カスタマーサポート担当 | 問い合わせの一次対応、回答文作成、エスカレーション判断 |
| `researcher` | リサーチャー | 出典付き調査メモの作成(Web 検索が既定で有効) |
| `backoffice` | 経理アシスタント | 経費・請求の突合、月次集計 |

`python -m ai_employee templates` で一覧できます。
`--role` / `--department` / `--mission` / `--web` で個別に上書きできます。

採用後の職務定義書は `ai-office/<社員ID>/profile.json` に置かれるので、
担当業務・行動指針・口調・権限は直接編集して調整できます。

## 社員が使えるツール

| ツール | 用途 |
| --- | --- |
| `current_datetime` | 現在日時・曜日の取得 |
| `record_note` / `search_notes` | 業務メモの記録と検索(社員の記憶) |
| `add_task` / `list_tasks` / `complete_task` | 自分のタスク管理 |
| `list_files` / `read_file` / `write_file` | 成果物の読み書き |
| `web_search` | Web 検索(`--web` で採用した社員のみ) |

`profile.json` の `tools` に列挙された名前だけが有効です。ここに書かれていないツールは
モデルに提示すらされないため、権限の付与・剥奪はこのファイルで完結します。

ファイル操作は `ai-office/<社員ID>/files/` の中に閉じ込められています。
`..` やシンボリックリンクを使ったワークスペース外へのアクセスは拒否されます。

## ワークスペースの中身

```
ai-office/<社員ID>/
├── profile.json     職務定義書(人間が編集する)
├── notes.jsonl      業務メモ(追記のみ)
├── tasks.json       タスク一覧
├── files/           成果物(社員が読み書きできる唯一の領域)
└── sessions/        日付ごとの会話ログ
```

置き場所は `--office` または環境変数 `AI_EMPLOYEE_HOME` で変更できます。

## コマンド一覧

| コマンド | 説明 |
| --- | --- |
| `hire` | AI社員を採用する |
| `roster` | 在籍者と未完了タスク数を一覧する |
| `ask` | 単発で業務を依頼する(`--remember` で当日の会話に引き継ぐ) |
| `chat` | 対話しながら業務を進める(`/exit` で終了、`/clear` で履歴消去) |
| `report` | 当日の記録から日報を書かせて `files/reports/` に保存する |
| `tasks` / `notes` | 社員のタスク・メモを人間が確認する |
| `templates` | 職種テンプレートを一覧する |

`--thinking` を付けると、社員の思考の要約もストリーミング表示されます。

## Python から使う

```python
from ai_employee import Employee, Workspace

ws = Workspace("sato")
employee = Employee(ws.load_profile(), ws)

result = employee.work("先週の A社案件の経緯をまとめて")
print(result.text)
print(result.tool_calls)   # 実際に使ったツール
print(result.ok)           # 拒否・打ち切りがなければ True
```

`Listener` を継承して `Employee(..., listener=...)` に渡すと、
本文・思考・ツール呼び出しをストリーミングで受け取れます(CLI の画面表示もこれを使っています)。

## 設計上の判断

- **エージェントループは手書き**。ツール実行の途中経過をトークン単位で表示したいこと、
  ベータ依存(Tool Runner)を避けたいことから、`client.beta.messages.stream` を使った
  手動ループにしています。並列ツール呼び出しの結果は 1 つの user メッセージにまとめて返します。
- **プロンプトキャッシュ**。system は「職務定義書(不変)→ 現在時刻と未完了タスク(揮発)」の
  順に組み立て、不変部分の末尾だけにキャッシュ区切りを置いています。
  ツールの並び順も指定順に依存しないよう定義順に固定しています。
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
ツール実行・並列呼び出し・拒否・出力上限・`pause_turn` 再開・上限打ち切りまで検証しています。
