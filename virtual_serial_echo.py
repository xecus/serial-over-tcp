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
import errno
import logging
import stat
from typing import Optional
from pathlib import Path


class VirtualSerialDevice:
    
    def _validate_device_path(self, path: str) -> bool:
        """デバイスパスの妥当性をチェック"""
        try:
            path_obj = Path(path)
            # Check if path is absolute and in safe location
            if not path_obj.is_absolute():
                return False
            
            # Prevent path traversal attacks
            if '..' in path_obj.parts:
                return False
            
            # Check parent directory exists and is writable
            parent = path_obj.parent
            if not parent.exists():
                return False
            
            if not os.access(parent, os.W_OK):
                return False
            
            return True
        except Exception:
            return False
    
    def _create_symlink_safely(self, target: str, link_path: str) -> bool:
        """競合状態を避けてシンボリックリンクを安全に作成"""
        try:
            # Create temporary symlink first
            self.temp_link_path = f"{link_path}.tmp.{os.getpid()}"
            os.symlink(target, self.temp_link_path)
            
            # Atomically move to final location
            os.rename(self.temp_link_path, link_path)
            self.temp_link_path = None
            return True
        except OSError as e:
            self.logger.error(f"シンボリックリンク作成エラー: {e}")
            # Clean up temp file if it exists
            if self.temp_link_path and os.path.exists(self.temp_link_path):
                try:
                    os.unlink(self.temp_link_path)
                except OSError:
                    pass
                self.temp_link_path = None
            return False
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
        self.temp_link_path = None  # For safe symlink creation
        
        # Setup logging
        logging.basicConfig(level=logging.INFO, 
                          format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
        # Validate device path
        if not self._validate_device_path(device_path):
            raise ValueError(f"Invalid device path: {device_path}")
        
        # プログラム終了時のクリーンアップを登録
        atexit.register(self.cleanup)
        
    def create_device(self) -> bool:
        """
        仮想シリアルデバイスを作成
        
        Returns:
            bool: 作成に成功した場合True
        """
        try:
            # 既存のデバイスファイルが存在する場合は安全に削除
            if os.path.exists(self.device_path) or os.path.islink(self.device_path):
                try:
                    # Check if it's a symlink we created
                    if os.path.islink(self.device_path):
                        os.unlink(self.device_path)
                        self.logger.info(f"既存のシンボリックリンクを削除しました: {self.device_path}")
                    else:
                        self.logger.error(f"既存のファイルがシンボリックリンクではありません: {self.device_path}")
                        return False
                except OSError as e:
                    if e.errno == errno.EACCES:
                        self.logger.error(f"ファイル削除の権限がありません: {self.device_path}")
                    else:
                        self.logger.error(f"既存ファイル削除エラー: {e}")
                    return False
            
            # 仮想端末ペアを作成
            self.master_fd, self.slave_fd = pty.openpty()
            
            # デバイスファイルへのシンボリックリンクを安全に作成
            slave_name = os.ttyname(self.slave_fd)
            if not self._create_symlink_safely(slave_name, self.device_path):
                return False
            
            # デバイスファイルのパーミッションを適切に設定（セキュリティ向上）
            try:
                os.chmod(self.device_path, 0o660)  # owner/group読み書き、other読み取り不可
            except OSError as e:
                self.logger.warning(f"パーミッション設定エラー: {e}")
            
            self.device_created = True
            
            self.logger.info(f"仮想シリアルデバイスを作成しました: {self.device_path}")
            self.logger.info(f"ボーレート: {self.baudrate}")
            self.logger.info(f"実際のデバイス: {slave_name}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"デバイス作成エラー: {e}")
            self.cleanup()  # Cleanup on failure
            return False
    
    def echo_handler(self):
        """
        エコーバック処理を行うメソッド
        """
        self.logger.info("エコーバック開始...")
        
        while self.running:
            try:
                # データが利用可能かチェック（タイムアウト付き）
                ready, _, _ = select.select([self.master_fd], [], [], 0.1)
                
                if ready:
                    # データを読み取り
                    data = os.read(self.master_fd, 1024)
                    if data:
                        # データサイズ制限チェック
                        if len(data) > 4096:
                            self.logger.warning(f"大きなデータパケット受信: {len(data)} bytes")
                            data = data[:4096]  # Truncate
                        
                        # 受信データをログ出力（安全な形式で）
                        safe_data = data[:50] if len(data) > 50 else data
                        self.logger.debug(f"受信: {safe_data}{'...' if len(data) > 50 else ''}")
                        
                        # 同じデータをエコーバック
                        try:
                            bytes_written = os.write(self.master_fd, data)
                            if bytes_written != len(data):
                                self.logger.warning(f"部分書き込み: {bytes_written}/{len(data)} bytes")
                            self.logger.debug(f"送信: {safe_data}{'...' if len(data) > 50 else ''}")
                        except OSError as e:
                            if e.errno == errno.EIO:
                                self.logger.info("デバイスが切断されました")
                                break
                            else:
                                raise
                        
            except OSError as e:
                if self.running:
                    if e.errno == errno.EIO:
                        self.logger.info("デバイスが切断されました")
                    elif e.errno == errno.EBADF:
                        self.logger.info("ファイルディスクリプタが無効です")
                    else:
                        self.logger.error(f"OSエラー: {e}")
                break
            except Exception as e:
                self.logger.error(f"予期しないエラー: {e}")
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
            
        self.logger.info("クリーンアップ処理を実行中...")
        
        # デバイスファイルを安全に削除
        if os.path.exists(self.device_path) or os.path.islink(self.device_path):
            try:
                os.unlink(self.device_path)
                self.logger.info(f"デバイスファイルを削除しました: {self.device_path}")
            except OSError as e:
                if e.errno == errno.EACCES:
                    # Try changing permissions first
                    try:
                        os.chmod(self.device_path, 0o777)
                        os.unlink(self.device_path)
                        self.logger.info(f"権限変更後にデバイスファイルを削除しました: {self.device_path}")
                    except OSError as e2:
                        self.logger.error(f"デバイスファイル削除失敗: {e2}")
                else:
                    self.logger.error(f"デバイスファイル削除エラー: {e}")
        
        # Clean up temporary symlink if it exists
        if self.temp_link_path and os.path.exists(self.temp_link_path):
            try:
                os.unlink(self.temp_link_path)
            except OSError:
                pass
        
        self.device_created = False
    
    def stop(self):
        """
        エコーバック処理を停止
        """
        self.logger.info("停止中...")
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


# Global variable for signal handler
device_instance = None

def signal_handler(signum, frame):
    """
    シグナルハンドラー（Ctrl+C対応）
    """
    global device_instance
    print(f"\n終了シグナル {signum} を受信しました...")
    # 強制終了前にクリーンアップを実行
    if device_instance:
        device_instance.stop()
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
            global device_instance
            device_instance = device
            
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
        logging.error(f"エラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
