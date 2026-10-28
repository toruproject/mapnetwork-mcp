[English](https://github.com/toruproject/mapnetwork-mcp/blob/main/README.md) | 日本語

# mapnetwork-mcp

[MapNetwork](https://mapnetwork.app) の MCP サーバーです。Claude などの MCP に対応した AI アシスタントから、日本の場所を、イラストのような見た目の地図の画像（PNG / SVG）にできます。

地図は、MapNetwork が日本向けに運用している Overpass API の、詳しい OpenStreetMap のデータから作ります。そのため、対象は**日本（国コード JP）**の場所です。日本の地名や施設の名前を伝えるだけで、細かな地図ができます。

## 特長

- 場所の名前、または複数のマーカーから地図を作れます
- 2つの場所の間の、徒歩・車のルートを重ねて描けます
- 11 のカラーテーマ（white、darkBlue、darkGreen、popArt、lightBlue、lightGreen、beige、magenta、gray、black、brown）
- PNG または SVG で出力し、ダウンロードフォルダーに保存します
- 作るのに時間がかかる地図も、自動で待ちます。`generate_*` のツールが中で完成を待ち、必要なら `download_map` で後から受け取れます
- 一度作った地図を、作り直さずに、別のカラーテーマや形式で取り直せます
- 作った地図を、MapNetwork の Web エディターで開いて、手で編集できます
- 作った地図を 3D で見られます。結果に付くリンクを開くと、お使いの PC のブラウザで 3D の地図を描き、画像として表示します（携帯端末では使えません）

## 使用例

**プロンプト：**「城崎温泉の外湯を地図にして。」

できた地図です。ダウンロードフォルダーに保存されます。

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-download.png" width="600" alt="城崎温泉の外湯の地図">

結果には、3つのリンクも付きます。ブラウザで開くと、作り直さずに、同じ地図をさらに活かせます。

**Download のリンク** — リンクの `colorSet` を書き換えると、カラーテーマを変えられます（この例は `&colorSet=black`）。

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-black.png" width="600" alt="同じ地図を black のカラーテーマにしたもの">

**View 3D のリンク** — お使いの PC のブラウザで 3D の地図を描き、画像として表示します。

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-view3d.png" width="600" alt="3D で描いた地図">

**Open in editor のリンク** — MapNetwork の Web エディターで、地図を手で編集できます（この例は、建物を消し、パッチワークをオンにし、popArt のカラーテーマにしたもの）。

<img src="https://raw.githubusercontent.com/toruproject/mapnetwork-mcp/main/images/KinosakiSotoyu-popArt.png" width="600" alt="Web エディターで編集した地図">

## インストール

### Claude Desktop

#### .mcpb の拡張機能ファイルを使う（おすすめ）

いちばん簡単なのは、[Releases](https://github.com/toruproject/mapnetwork-mcp/releases) のページにある `.mcpb` の拡張機能ファイルを使う方法です。

1. Releases のページから、最新の `.mcpb` ファイルをダウンロードします。
2. Claude Desktop を開き、**Settings**（設定）→ **Extensions**（拡張機能）を開きます。
3. ダウンロードした `.mcpb` ファイルを、拡張機能の画面にドラッグしてインストールします。

#### 手で設定する

`claude_desktop_config.json` に、次の内容を加えます。

```json
{
  "mcpServers": {
    "mapnetwork": {
      "command": "uvx",
      "args": ["mapnetwork-mcp"],
      "alwaysAllow": ["generate_map", "generate_markers_map", "generate_route_map", "download_map", "redownload_map"]
    }
  }
}
```

`uvx` の代わりに `pip` を使う場合：

```bash
pip install mapnetwork-mcp
```

```json
{
  "mcpServers": {
    "mapnetwork": {
      "command": "mapnetwork-mcp"
    }
  }
}
```

## リモート MCP サーバー

何もインストールせずに、リモート MCP サーバーから MapNetwork を使うこともできます。

```
https://mapnetwork.app/mcp
```

Claude で **Settings**（設定）→ **Connectors**（コネクタ）→ **Add custom connector**（カスタムコネクタを追加）を開き、上の URL を登録します。

このローカルのサーバーとの大きな違いは、できた地図の画像の受け取り方です。`mapnetwork-mcp` はお使いの PC で動くので、できた画像を、ダウンロードフォルダーに直接保存できます。リモートのサーバーにはこれができません（多くの AI アシスタントは、ツールの結果の画像を、チャットの中でうまく表示・保存できません）。そのため、画像のデータの代わりに、[mapnetwork.app](https://mapnetwork.app) の3つのリンクを、文章で返します。PNG のダウンロードのリンク、地図を編集するリンク、地図を 3D で見るリンクです（3D の地図は、お使いの PC のブラウザで描かれ、数秒後に画像として表示されます。携帯端末では使えません）。リンクをブラウザで開くと、画像を見たり保存したり、地図を編集したり、3D で見たりできます。ローカルのサーバーも、PNG を保存した後に、同じ3つのリンクを返します。

## ツール

| ツール | 説明 |
|---|---|
| `generate_map` | 1つの場所を中心にした地図の画像を作り、ダウンロードフォルダーに保存します |
| `generate_markers_map` | 複数の場所にピンを立てた地図を作ります |
| `generate_route_map` | 2つの場所の間の徒歩・車のルートを求め、それを重ねた地図を作ります |
| `download_map` | 作っている途中の `generate_*` の地図を確かめ、できていれば受け取ります |
| `redownload_map` | 一度作った地図を、別のカラーテーマや形式で取り直します。作り直さないので、すぐにできます |

## プロンプトの例

- 「東京タワー周辺の地図を作って」
- 「上野駅が最寄りの美術館・博物館を地図にして」
- 「赤坂駅から赤坂氷川神社までの徒歩ルートを地図にして」
- 「その地図を darkBlue テーマでSVG画像で出し直して」
- 「生成した地図の編集用リンクをください」

## リンク

- Web UI：[https://mapnetwork.app](https://mapnetwork.app)
- Web UI のマニュアル：[https://mapnetwork.app/manual](https://mapnetwork.app/manual)
- API のドキュメント：[https://mapnetwork.app/openapi.json](https://mapnetwork.app/openapi.json)
