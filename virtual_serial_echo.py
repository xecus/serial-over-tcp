#!/usr/bin/env python3
"""
仮想シリアルデバイスを作成し、送信された内容をエコーバックするスクリプト
"""

import os
import pty
import select
import signal
import sys
import threading
import time
import argparse
import atexit
from typing import Optional


class VirtualSerialDevice:
    def __init__(self, device_path: str, baudrate: int = 9600):
        """
        仮想シリアルデバイスを初期化
        
        Args:
            device_path: 作成するデバイスファイルのパス
            baudrate: ボーレート（デフォルト: 9600）
        """
        self.device_path = device_path
        self.baudrate = baudrate
        self.master_fd = None
        self.slave_fd = None
        self.running = False
        self.echo_thread = None
        self.device_created = False
        
        # プログラム終了時のクリーンアップを登録
        atexit.register(self.cleanup)
        
    def create_device(self) -> bool:
        """
        仮想シリアルデバイスを作成
        
        Returns:
            bool: 作成に成功した場合True
        """
        try:
            # 既存のデバイスファイルが存在する場合は削除
            if os.path.exists(self.device_path):
                try:
                    os.unlink(self.device_path)
                    print(f"既存のデバイスファイルを削除しました: {self.device_path}")
                except Exception as e:
                    print(f"既存ファイル削除警告: {e}")
            
            # 仮想端末ペアを作成
            self.master_fd, self.slave_fd = pty.openpty()
            
            # デバイスファイルへのシンボリックリンクを作成
            slave_name = os.ttyname(self.slave_fd)
            os.symlink(slave_name, self.device_path)
            
            # デバイスファイルのパーミッションを設定
            os.chmod(self.device_path, 0o666)
            
            self.device_created = True
            
            print(f"仮想シリアルデバイスを作成しました: {self.device_path}")
            print(f"ボーレート: {self.baudrate}")
            print(f"実際のデバイス: {slave_name}")
            
            return True
            
        except Exception as e:
            print(f"デバイス作成エラー: {e}")
            return False
    
    def echo_handler(self):
        """
        エコーバック処理を行うメソッド
        """
        print("エコーバック開始...")
        
        while self.running:
            try:
                # データが利用可能かチェック（タイムアウト付き）
                ready, _, _ = select.select([self.master_fd], [], [], 0.1)
                
                if ready:
                    # データを読み取り
                    data = os.read(self.master_fd, 1024)
                    if data:
                        # 受信データをログ出力
                        print(f"受信: {data}")
                        
                        # 同じデータをエコーバック
                        os.write(self.master_fd, data)
                        print(f"送信: {data}")
                        
            except OSError as e:
                if self.running:
                    print(f"エコーバック処理エラー: {e}")
                break
            except Exception as e:
                print(f"予期しないエラー: {e}")
                break
    
    def start(self):
        """
        エコーバック処理を開始
        """
        if not self.create_device():
            return False
        
        self.running = True
        
        # エコーバック処理を別スレッドで開始
        self.echo_thread = threading.Thread(target=self.echo_handler)
        self.echo_thread.daemon = True
        self.echo_thread.start()
        
        return True
    
    def cleanup(self):
        """
        クリーンアップ処理（確実にデバイスファイルを削除）
        """
        if not self.device_created:
            return
            
        print("クリーンアップ処理を実行中...")
        
        # デバイスファイルを削除
        if os.path.exists(self.device_path):
            try:
                os.unlink(self.device_path)
                print(f"デバイスファイルを削除しました: {self.device_path}")
            except Exception as e:
                print(f"デバイスファイル削除エラー: {e}")
                # 強制削除を試行
                try:
                    os.system(f"rm -f {self.device_path}")
                    print(f"強制削除を実行しました: {self.device_path}")
                except:
                    print(f"強制削除も失敗しました: {self.device_path}")
        
        self.device_created = False
    
    def stop(self):
        """
        エコーバック処理を停止
        """
        print("停止中...")
        self.running = False
        
        # スレッドの終了を待機
        if self.echo_thread:
            self.echo_thread.join(timeout=2.0)
        
        # ファイルディスクリプタを閉じる
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except:
                pass
        if self.slave_fd:
            try:
                os.close(self.slave_fd)
            except:
                pass
        
        # クリーンアップ処理を実行
        self.cleanup()
    
    def __enter__(self):
        """
        コンテキストマネージャーの開始
        """
        if self.start():
            return self
        else:
            raise RuntimeError("仮想シリアルデバイスの作成に失敗しました")
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        コンテキストマネージャーの終了
        """
        self.stop()


def signal_handler(signum, frame):
    """
    シグナルハンドラー（Ctrl+C対応）
    """
    print("\n終了シグナルを受信しました...")
    # 強制終了前にクリーンアップを実行
    if hasattr(signal_handler, 'device_instance'):
        signal_handler.device_instance.cleanup()
    sys.exit(0)


def main():
    """
    メイン関数
    """
    parser = argparse.ArgumentParser(description='仮想シリアルデバイス エコーサーバー')
    parser.add_argument('device_path', help='作成するデバイスファイルのパス')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, 
                       help='ボーレート (デフォルト: 9600)')
    
    args = parser.parse_args()
    
    # シグナルハンドラーを設定
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 仮想シリアルデバイスを作成・開始
    try:
        with VirtualSerialDevice(args.device_path, args.baudrate) as device:
            # シグナルハンドラーからアクセスできるようにする
            signal_handler.device_instance = device
            
            print(f"エコーサーバーが動作中です...")
            print(f"デバイス: {args.device_path}")
            print("終了するにはCtrl+Cを押してください")
            
            # メインループ
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n終了します...")
                
    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
