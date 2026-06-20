# ngit

ngit は、Notion データベース上に作成したファイルツリーを、ローカル環境へクローンするための小さな CLI ツールです。

Notion のページをフォルダまたはファイルとして扱い、ページ本文のコードブロックをローカルファイルとして保存します。初回セットアップ後は、指定したルート名をもとに `ngit clone` でファイル一式を取得できます。

## 1. ngit フォルダへ移動する

~~~shell
cd ngit
~~~

以降のコマンドは、基本的に `ngit` フォルダ直下で実行します。

## 2. Notion Integration を作成する

Notion API でデータベースを読むために、ngit 用の Notion Integration を作成します。

1. Notion Developers の My integrations を開く
2. New integration を選ぶ
3. Integration 名を `ngit` などにする
4. 使用する Workspace を選ぶ
5. Capability は読み取り系を有効にする
    - Content: Read content
    - Comment: 不要
    - User information: 基本不要
6. 作成後、Internal Integration Secret を表示してコピーする

コピーした Secret は、後で `.env` の `NOTION_API_TOKEN` に設定します。

## 3. Notion DB に Integration を接続する

クローン元にする Notion DB を、作成した Integration に接続します。

1. Notion でクローン元の DB を開く
2. 右上の `...` または共有メニューを開く
3. Connections / コネクション追加から、作成した `ngit` Integration を選ぶ
4. Integration が DB を読める状態になっていることを確認する

この接続を忘れると、ngit から DB を取得できません。

## 4. Notion DB ID を確認する

クローン元にする DB の ID を控えます。

1. Notion でクローン元 DB をフルページで開く
2. 右上の `...` から Copy link を選ぶ
3. URL 内の DB ID 部分を控える

控えた値は、後で `.env` の `NOTION_PROJECT_DATABASE_ID` に設定します。

## 5. `_.env` を `.env` にリネームして編集する

`_.env` を `.env` にリネームします。

PowerShell:

~~~shell
Rename-Item _.env .env
~~~

macOS / Linux:

~~~shell
mv _.env .env
~~~

作成した `.env` を開いて、必要な値を直接入力します。

~~~text
NOTION_API_TOKEN=secret_xxx
NOTION_PROJECT_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

NGIT_NAME_PROPERTY=name
NGIT_DIR_PROPERTY=dir
NGIT_PARENT_PROPERTY=parent
~~~

設定内容:

- `NOTION_API_TOKEN`
    - Notion Integration の Internal Integration Secret
- `NOTION_PROJECT_DATABASE_ID`
    - クローン元にする Notion DB の ID
- `NGIT_NAME_PROPERTY`
    - ファイル名 / フォルダ名として使う title プロパティ名
- `NGIT_DIR_PROPERTY`
    - フォルダ判定に使う checkbox プロパティ名
- `NGIT_PARENT_PROPERTY`
    - 親フォルダ判定に使う relation プロパティ名

DB のプロパティ名が異なる場合は、`.env` 側を DB に合わせて変更します。

例: 親 relation が `親アイテム` という名前の場合

~~~text
NGIT_PARENT_PROPERTY=親アイテム
~~~

## 6. 依存関係を同期する

~~~shell
uv sync
~~~

## 7. ヘルプ表示を確認する

~~~shell
uv run python ngit.py --help
~~~

## 8. ルートフォルダ一覧を確認する

クローン対象にできるルートフォルダを一覧表示します。

ルートフォルダは、Notion DB 上で親 relation が空、かつ `dir = true` のページとして判定されます。

~~~shell
uv run python ngit.py list
~~~

uv tool としてインストール済みの場合:

~~~shell
ngit list
~~~

## 9. dry-run でクローン予定を確認する

まずはファイルを書き込まずに確認します。

~~~shell
uv run python ngit.py clone my-repository --dry-run
~~~

## 10. ローカルへクローンする

dry-run の内容に問題がなければ、実際にファイルを書き込みます。

~~~shell
uv run python ngit.py clone my-repository
~~~

既存ファイルがある場合、デフォルトでは上書きせずスキップします。

## 11. 上書きしてクローンする場合

Notion 側の内容で既存ファイルを上書きしたい場合のみ、`--force` を付けます。

~~~shell
uv run python ngit.py clone my-repository --force
~~~

> [!NOTE]
> `--force` は既存ファイルを上書きします。
> ローカルで編集した内容も上書きされるため、必要な場合のみ使ってください。
> 不安な場合は、先に別フォルダへクローンして確認してください。

## 12. 任意の場所から `ngit` を呼び出す場合

毎回 `uv run python ngit.py ...` と入力する代わりに、uv tool としてインストールできます。

~~~shell
uv tool install --editable .
~~~

PATH が通っていない場合は、次を実行してから PowerShell を開き直します。

~~~shell
uv tool update-shell
~~~

確認:

~~~shell
ngit --help
~~~

以後、任意のディレクトリから実行できます。

~~~shell
ngit clone my-repository
~~~

## 実行オプションについて

`ngit.py`（または `ngit` コマンド）には、以下のコマンドがあります。

- `list`
    - クローン対象にできるルートフォルダを一覧表示します。
    - 親 relation が空で、`dir = true` のページだけを表示します。
    - 例:
        - `uv run python ngit.py list`
        - `ngit list`

- `clone <root_name>`
    - 指定したルートフォルダ配下のファイルツリーをローカルへクローンします。
    - 例:
        - `uv run python ngit.py clone my-repository`
        - `ngit clone my-repository`

`clone` コマンドには、挙動を調整するための代表的なオプションがあります。

- `--dry-run`
    - **実際にはファイルを書き込まず**、作成・更新予定の内容だけを表示します。
    - 初回実行や、DB のプロパティ名を変更した直後の確認に便利です。
    - 例:
        - `uv run python ngit.py clone my-repository --dry-run`

- `--force`
    - 既存ファイルがある場合でも **上書きして** クローンします。
    - ローカルで編集した内容も上書きされるため、必要な場合のみに使ってください。
    - 例:
        - `uv run python ngit.py clone my-repository --force`

- `--to <PATH>`
    - 出力先ディレクトリ（クローン先）を指定します。
    - 指定しない場合は、コマンド実行時のカレントディレクトリ（`.` 相当）に出力されます（挙動はバージョン/実装により変わる可能性があるため、確実にしたい場合は明示指定してください）。
    - 例:
        - `uv run python ngit.py clone my-repository --to .`
        - `ngit clone my-repository --to C:\path\to\repo`

- `--env-file <PATH>`
    - 読み込む `.env` ファイルを明示します。
    - 既定では通常、リポジトリ直下の `.env` が読み込まれます。
    - 例:
        - `ngit --env-file C:\path\to\ngit\.env clone transcript-collector --to .`
