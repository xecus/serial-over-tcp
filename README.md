# シリアル通信ツールキット

Python で実装されたシリアル通信ブリッジとエミュレーションツールのセットです。

## 概要

このリポジトリには、シリアルポートのネットワーク化と仮想化のための3つの主要なユーティリティが含まれています：

1. **serial_tcp_server.py** - 物理シリアルポートをTCP接続にブリッジする ser2net 相当のツール
2. **serial_tcp_client.py** - TCP サーバーに接続する仮想シリアルデバイスを作成
3. **virtual_serial_echo.py** - 受信したデータをエコーバックする仮想シリアルデバイス

## 依存関係

- Python 3.x
- pyserial ライブラリ（serial_tcp_server.py でのみ必要）

### インストール

```bash
pip install pyserial
```

## 使用方法

### 1. シリアル TCP サーバー (ser2net 相当)

物理シリアルポートをTCPネットワーク経由でアクセス可能にします。

```bash
# 基本的な使用法
python3 serial_tcp_server.py /dev/ttyUSB0 9999

# 詳細オプション付き
python3 serial_tcp_server.py /dev/ttyUSB0 9999 -b 9600 -d 8 -p N -s 1 -v
```

**オプション:**
- `-b, --baudrate`: ボーレート (デフォルト: 9600)
- `-d, --databits`: データビット数 (5,6,7,8)
- `-p, --parity`: パリティ (N=なし, E=偶数, O=奇数, M=マーク, S=スペース)
- `-s, --stopbits`: ストップビット (1, 1.5, 2)
- `-t, --timeout`: シリアルタイムアウト（秒）
- `-v, --verbose`: 詳細ログ出力

### 2. シリアル TCP クライアント

TCP サーバーに接続し、仮想シリアルデバイスを作成します。

```bash
# 基本的な使用法
python3 serial_tcp_client.py localhost 9999

# カスタムデバイスパス指定
python3 serial_tcp_client.py localhost 9999 -d /tmp/vserial0
```

**オプション:**
- `-d, --device`: 仮想デバイスのパス（実際のptyデバイスへのシンボリックリンクを作成）
- `-v, --verbose`: 詳細ログ出力

**仮想デバイスへの接続:**
```bash
# screen を使用
screen /tmp/vserial0 9600

# minicom を使用
minicom -D /tmp/vserial0

# その他のシリアル通信ソフトウェアでも使用可能
```

### 3. 仮想シリアルエコーデバイス

送信されたデータをそのままエコーバックする仮想シリアルデバイスを作成します。

```bash
# 基本的な使用法
python3 virtual_serial_echo.py /tmp/echo_device

# ボーレート指定
python3 virtual_serial_echo.py /tmp/echo_device -b 9600
```

**オプション:**
- `-b, --baudrate`: ボーレート (デフォルト: 9600)

## 使用例

### 例1: リモートシリアルデバイスアクセス

1. サーバー側で物理シリアルポートを公開：
```bash
python3 serial_tcp_server.py /dev/ttyUSB0 9999 -b 115200
```

2. クライアント側で仮想シリアルデバイスを作成：
```bash
python3 serial_tcp_client.py server_ip 9999 -d /tmp/remote_serial
```

3. 仮想デバイスを使用：
```bash
screen /tmp/remote_serial 115200
```

### 例2: シリアル通信のテスト

1. エコーデバイスを作成：
```bash
python3 virtual_serial_echo.py /tmp/test_device
```

2. 別のターミナルで接続・テスト：
```bash
screen /tmp/test_device 9600
# 何か入力すると同じ内容がエコーバックされます
```

## アーキテクチャ

### マルチスレッド設計
- メインスレッド：セットアップとシグナルハンドリング
- データ転送スレッド：双方向データ転送処理
- select() を使用したノンブロッキング I/O

### 仮想デバイス作成
- Python の `pty` モジュールを使用して擬似端末ペアを作成
- カスタムデバイスパス用のシンボリックリンク作成
- シャットダウン時の自動クリーンアップ

### エラーハンドリング
- 包括的な例外処理とログ出力
- デバイス利用不可時の優雅な劣化
- クリーンシャットダウンのためのシグナルハンドラー

## 注意事項

- 仮想デバイスは適切な権限で実行する必要があります
- シンボリックリンクの作成にはファイルシステムの書き込み権限が必要です
- Ctrl+C で安全に停止できます（自動クリーンアップ実行）

## ライセンス

このプロジェクトはフリーソフトウェアです。
