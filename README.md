# ngit

ngit は、Notion データベース上に作成したファイルツリーを、ローカル環境と同期するための CLI ツールです。

Notion のページをフォルダまたはファイルとして扱い、ファイルページ本文の単一コードブロックをローカルファイルの内容として扱います。

## コマンド概要

- `ngit list`
    - Notion DB 上のルートフォルダを一覧表示します。
- `ngit clone <root_name>`
    - Notion 側のルートフォルダを指定して、ローカルへ新規複製します。
- `ngit pull [paths...]`
    - Git リポジトリルートを自動解決し、Notion 側の内容をローカルへ反映します。
    - ファイル / フォルダは複数指定できます。
- `ngit push [paths...]`
    - Git リポジトリルートを自動解決し、ローカル側の内容を Notion へ反映します。
    - ファイル / フォルダは複数指定できます。

`pull` / `push` は差分管理、マージ、コンフリクト解決を行いません。`pull` / `push` ともに書き込み先にデータが存在する場合は、書き込みをスキップします。

## 1. ngit フォルダへ移動する

~~~shell
cd ngit
~~~

## 2. Notion Integration を作成する

Notion API でデータベースを読むために、ngit 用の Notion Integration を作成します。

1. Notion Developers の My integrations を開く
2. New integration を選ぶ
3. Integration 名を `ngit` などにする
4. 使用する Workspace を選ぶ
5. Capability を設定する
    - Content: Read content / Update content / Insert content
    - Comment: 不要
    - User information: 基本不要
6. 作成後、Internal Integration Secret を表示してコピーする

コピーした Secret は、後で `.env` の `NOTION_API_TOKEN` に設定します。

## 3. Notion DB に Integration を接続する

同期元 / 同期先にする Notion DB を、作成した Integration に接続します。

1. Notion で対象 DB を開く
2. 右上の `...` または共有メニューを開く
3. Connections / コネクション追加から、作成した `ngit` Integration を選ぶ
4. Integration が DB を読める / 更新できる状態になっていることを確認する

この接続を忘れると、ngit から DB を取得・更新できません。

## 4. Notion DB ID を確認する

対象 DB の ID を控えます。

1. Notion で対象 DB をフルページで開く
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

作成した `.env` を開いて、必要な値を入力します。

~~~text
NOTION_API_TOKEN=secret_xxx
NOTION_PROJECT_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

NGIT_NAME_PROPERTY=name
NGIT_DIR_PROPERTY=dir
NGIT_PARENT_PROPERTY=parent
NGIT_DIR_PAGE_ICON=icons/folder_yellow
NGIT_FILE_PAGE_ICON=icons/document_blue
~~~

設定内容:

- `NOTION_API_TOKEN`
    - Notion Integration の Internal Integration Secret
- `NOTION_PROJECT_DATABASE_ID`
    - 同期対象にする Notion DB の ID
- `NGIT_NAME_PROPERTY`
    - ファイル名 / フォルダ名として使う title プロパティ名
- `NGIT_DIR_PROPERTY`
    - フォルダ判定に使う checkbox プロパティ名
- `NGIT_PARENT_PROPERTY`
    - 親フォルダ判定に使う relation プロパティ名
- `NGIT_DIR_PAGE_ICON`
    - `push` で新規作成する通常ディレクトリページのアイコン
    - 既定値は `icons/folder_yellow`
- `NGIT_FILE_PAGE_ICON`
    - `push` で新規作成するファイルページのアイコン
    - 既定値は `icons/document_blue`

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

uv tool としてインストール済みの場合:

~~~shell
ngit --help
~~~

## list

同期対象にできるルートフォルダを一覧表示します。

ルートフォルダは、Notion DB 上で親 relation が空、かつ `dir = true` のページとして判定されます。

~~~shell
uv run python ngit.py list
~~~

uv tool としてインストール済みの場合:

~~~shell
ngit list
~~~

## clone

Notion 上のルートフォルダを指定し、ローカルに新規複製します。

~~~shell
uv run python ngit.py clone my-repository
~~~

出力先を指定する場合:

~~~shell
uv run python ngit.py clone my-repository --to ./work
~~~

dry-run:

~~~shell
uv run python ngit.py clone my-repository --dry-run
~~~

既存ファイルを上書きする場合:

~~~shell
uv run python ngit.py clone my-repository --force
~~~

### clone のオプション

- `root_name`
    - 必須。
    - Clone 対象の Notion 側ルートフォルダ名です。
- `--to <PATH>`
    - 任意。
    - 出力先ディレクトリを指定します。
    - 指定しない場合はカレントディレクトリに出力されます。
- `--dry-run`
    - 任意。
    - 実際には作成・更新せず、予定だけを表示します。
- `--force`
    - 任意。
    - 既存ファイルがある場合に上書きします。

## pull

Git リポジトリ内で実行し、Notion 側の内容をローカルのリポジトリルートへ反映します。

~~~shell
uv run python ngit.py pull
~~~

部分的に同期する場合:

~~~shell
uv run python ngit.py pull README.md
uv run python ngit.py pull src
uv run python ngit.py pull src/transcript_collector/cli.py
uv run python ngit.py pull README.md src/transcript_collector/cli.py
~~~

dry-run:

~~~shell
uv run python ngit.py pull --dry-run
uv run python ngit.py pull src --dry-run
~~~

既存ファイルを上書きし、Notion 側に存在しないローカルファイル / フォルダを削除する場合:

~~~shell
uv run python ngit.py pull --force
~~~

### pull のオプション

- `paths`
    - 任意。
    - 同期対象のフォルダまたはファイルです。
    - 複数指定できます。
    - Git リポジトリルートからの相対パスとして解釈されます。
    - 省略した場合はリポジトリ全体を対象にします。
- `--dry-run`
    - 任意。
    - 実際には作成・更新・削除せず、同期予定を表示します。
- `--force`
    - 任意。
    - 既存ファイルがある場合に上書きします。
    - 対象範囲内で Notion 側に存在しないローカルファイル / フォルダを削除します。

### pull の仕様

- カレントディレクトリから上方向に Git リポジトリを探索します。
- `git rev-parse --show-toplevel` でリポジトリルートを取得します。
- リポジトリルートのディレクトリ名を Notion 側のルートフォルダ名として扱います。
- 同期先は常に Git リポジトリルートです。
- `--force` 指定時は、対象範囲内で Notion 側に存在しないローカルファイル / フォルダを削除します。ただし、`.gitignore` 対象のローカルファイル / フォルダは削除しません。
- `.git/` ディレクトリは常に削除対象外です。
- 差分管理、マージ、コンフリクト解決は行いません。

## push

Git リポジトリ内で実行し、ローカル側の内容を Notion 側のツリーへ反映します。

~~~shell
uv run python ngit.py push
~~~

部分的に同期する場合:

~~~shell
uv run python ngit.py push README.md
uv run python ngit.py push src
uv run python ngit.py push src/transcript_collector/cli.py
uv run python ngit.py push README.md src/transcript_collector/cli.py
~~~

dry-run:

~~~shell
uv run python ngit.py push --dry-run
uv run python ngit.py push src --dry-run
~~~

ローカルに存在しない Notion ページを削除対象にする場合:

~~~shell
uv run python ngit.py push --force
uv run python ngit.py push src --force
~~~

### push のオプション

- `paths`
    - 任意。
    - push 対象のフォルダまたはファイルです。
    - 複数指定できます。
    - Git リポジトリルートからの相対パスとして解釈されます。
    - 省略した場合はリポジトリ全体を対象にします。
- `--dry-run`
    - 任意。
    - 実際には Notion を作成・更新・削除せず、push 予定を表示します。
- `--force`
    - 任意。
    - 既存 Notion ページへの書き込みを許可します。
    - 対象範囲内でローカルに存在しない Notion ページを削除対象にします。

### push の仕様

- カレントディレクトリから上方向に Git リポジトリを探索します。
- `git rev-parse --show-toplevel` でリポジトリルートを取得します。
- リポジトリルートのディレクトリ名を Notion 側のルートフォルダ名として扱います。
- Git の追跡状態は参照しません。
- 実行時点の `.gitignore` 解決結果のみを正本として push 対象を決めます。
- Git で追跡済みのファイルであっても、`.gitignore` 対象なら push しません。
- `.git/` ディレクトリは常に対象外です。
- `.env` など Notion へ送信したくないファイルは、リポジトリの `.gitignore` に追加してください。
- ローカルに存在するファイルまたはフォルダに対応する Notion ページが存在しない場合は、新規ページを作成します。
- ローカルファイルに対応する Notion ページがすでに存在する場合、デフォルトでは書き込みません。
- 既存 Notion ページをローカル内容で更新する場合は `--force` を指定します。
- 新規作成する通常ディレクトリページのアイコンは、既定では `icons/folder_yellow` です。
- 新規作成するファイルページのアイコンは、既定では `icons/document_blue` です。
- ローカルに存在しない Notion ページは、デフォルトでは削除しません。
- `--force` 指定時のみ、対象範囲内でローカルに存在しない Notion ページを削除対象にします。

## 任意の場所から `ngit` を呼び出す場合

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
ngit pull
ngit push
~~~

`.env` の場所を明示したい場合は、グローバルオプション `--env-file` を使います。

~~~shell
ngit --env-file C:\path\to\ngit\.env pull
ngit --env-file C:\path\to\ngit\.env push src
~~~

## トラブルシューティング

### `Insufficient permissions for this endpoint.` が出る場合

`ngit push` は Notion ページの作成・更新・削除を行うため、Notion Integration に書き込み系の権限が必要です。

Integration の Capability で以下を有効にしてください。

- Content: Read content
- Content: Insert content
- Content: Update content

権限を変更した後、必要に応じて対象 DB / ページに Integration を接続し直してください。

### `.env` が push 対象になる場合

`push` は Git の追跡状態を参照せず、実行時点の `.gitignore` だけを見ます。

`.env` を Notion へ送信したくない場合は、リポジトリの `.gitignore` に追加してください。

~~~gitignore
.env
~~~

## 注意

- ファイルページ本文は、単一のコードブロックだけを含む前提です。
- コードブロック外に内容を持つ本文ブロックがある場合、ngit はエラーにします。
- `pull --force` はローカルファイルを上書きし、対象範囲内で Notion 側に存在しないローカルファイル / フォルダを削除します。ただし、`.gitignore` 対象は削除しません。
- `push --force` は既存 Notion ページへの書き込みを許可し、対象範囲内でローカルに存在しない Notion ページを削除対象にします。
- 不安な場合は、先に `--dry-run` で確認してください。
