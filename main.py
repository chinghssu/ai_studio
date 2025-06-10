import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkvideo import tkvideo
import threading
import queue
import requests
import subprocess
import logging
import datetime
import base64
import json
#加一個失敗重生的按鈕
from pathlib import Path
from tenacity import retry, wait_exponential, stop_after_attempt
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.message import EmailMessage
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from tkinter import scrolledtext
import shutil
from dotenv import load_dotenv
load_dotenv()
vidu_key = os.getenv("VIDU_API_KEY")
# 載入環境變數
class Config:
    """配置管理類"""
    def __init__(self):
        self.config_file = "config.json"
        self.default_config = {
            "vidu_key": vidu_key,
            "ae_exe": r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe",
            "ae_project": r"C:\Users\Anchor_LP5\Desktop\test_1.aep",
            "ae_script": r"C:\Users\Anchor_LP5\Desktop\minimal_render.jsx",
            "output_dir": r"C:\Users\Anchor_LP5\Desktop\ai_studio_files",
            "gmail_from": "chinghssu@gmail.com"
        }
        self.load_config()
    
    def load_config(self):
        """載入配置"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    self.config = {**self.default_config, **config_data}
            else:
                self.config = self.default_config.copy()
                self.save_config()
        except Exception as e:
            logging.error(f"載入配置失敗: {e}")
            self.config = self.default_config.copy()
    
    def save_config(self):
        """儲存配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"儲存配置失敗: {e}")
    
    def get(self, key):
        return self.config.get(key)
    
    def set(self, key, value):
        self.config[key] = value
        self.save_config()

class NetworkChecker:
    """網路連線檢查工具"""
    
    @staticmethod
    def check_internet_connection() -> tuple[bool, str]:
        """檢查網路連線"""
        test_urls = [
            "https://www.google.com",
            "https://www.cloudflare.com",
            "https://httpbin.org/get"
        ]
        
        for url in test_urls:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    return True, "網路連線正常"
            except:
                continue
        
        return False, "無法連接到網際網路"
    
    @staticmethod
    def check_vidu_api_access(api_key: str) -> tuple[bool, str]:
        """檢查 Vidu API 連線"""
        if not api_key:
            return False, "API Key 未設定"
        
        try:
            # 嘗試簡單的 API 請求來測試連線
            response = requests.get(
                "https://api.vidu.ai/jobs",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10
            )
            
            if response.status_code == 401:
                return False, "API Key 無效"
            elif response.status_code == 403:
                return False, "API Key 權限不足"
            elif response.status_code == 429:
                return False, "API 請求次數過多"
            elif response.status_code < 500:
                return True, "Vidu API 連線正常"
            else:
                return False, f"Vidu 伺服器錯誤: {response.status_code}"
                
        except requests.exceptions.ConnectionError:
            return False, "無法連接到 Vidu API"
        except requests.exceptions.Timeout:
            return False, "Vidu API 連線超時"
        except Exception as e:
            return False, f"API 連線檢查失敗: {str(e)}"

class VideoProcessor:
    """影片處理類"""
    
    def __init__(self, config):
        self.config = config
        self.SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    
    def call_vidu(self, image_path: str) -> str:
        """上傳圖片到 Vidu API"""
        if not self.config.get('vidu_key'):
            raise ValueError("Vidu API Key 未設定")
        
        logging.info(f"上傳至 Vidu: {image_path}")
        
        # 上傳階段的重試
        job_id = self._upload_to_vidu(image_path)
        
        # 輪詢階段
        return self._poll_vidu_status(job_id)
    
    @retry(wait=wait_exponential(multiplier=2, min=1, max=10), 
           stop=stop_after_attempt(5))
    def _upload_to_vidu(self, image_path: str) -> str:
        """上傳圖片到 Vidu（帶重試）"""
        try:
            # 設定 requests session 以便重用連線
            session = requests.Session()
            session.headers.update({
                "Authorization": f"Bearer {self.config.get('vidu_key')}",
                "User-Agent": "AI-Booth/1.0"
            })
            
            with open(image_path, "rb") as img_file:
                files = {"image": img_file}
                response = session.post(
                    "https://api.vidu.ai/jobs",
                    files=files,
                    timeout=(10, 60)  # (連線超時, 讀取超時)
                )
                
            # 檢查回應狀態
            if response.status_code == 401:
                raise ValueError("Vidu API Key 無效或已過期")
            elif response.status_code == 429:
                raise requests.RequestException("API 請求次數過多，請稍後再試")
            elif response.status_code >= 500:
                raise requests.RequestException(f"Vidu 伺服器錯誤: {response.status_code}")
            elif not response.ok:
                raise requests.RequestException(f"Vidu API 錯誤: {response.status_code} - {response.text}")
            
            try:
                resp_data = response.json()
            except ValueError as e:
                raise requests.RequestException(f"API 回應格式錯誤: {e}")
                
            if "id" not in resp_data:
                raise requests.RequestException(f"API 回應缺少 job ID: {resp_data}")
                
            return resp_data["id"]
            
        except requests.exceptions.ConnectionError as e:
            logging.error(f"網路連線錯誤: {e}")
            raise requests.RequestException("無法連線到 Vidu API，請檢查網路連線")
        except requests.exceptions.Timeout as e:
            logging.error(f"請求超時: {e}")
            raise requests.RequestException("Vidu API 請求超時")
        except requests.exceptions.RequestException as e:
            logging.error(f"API 請求錯誤: {e}")
            raise
        except Exception as e:
            logging.error(f"未預期的錯誤: {e}")
            raise requests.RequestException(f"上傳失敗: {str(e)}")
    
    def _poll_vidu_status(self, job_id: str) -> str:
        """輪詢 Vidu 任務狀態"""
        import time
        max_attempts = 180  # 最多等待6分鐘
        consecutive_failures = 0
        max_consecutive_failures = 3
        
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {self.config.get('vidu_key')}",
            "User-Agent": "AI-Booth/1.0"
        })
        
        for attempt in range(max_attempts):
            try:
                status_response = session.get(
                    f"https://api.vidu.ai/jobs/{job_id}",
                    timeout=(5, 15)
                )
                
                if not status_response.ok:
                    if status_response.status_code == 404:
                        raise Exception(f"找不到任務 ID: {job_id}")
                    elif status_response.status_code >= 500:
                        consecutive_failures += 1
                        logging.warning(f"伺服器錯誤 {status_response.status_code}，第 {consecutive_failures} 次")
                        if consecutive_failures >= max_consecutive_failures:
                            raise Exception("伺服器持續錯誤，請稍後再試")
                        time.sleep(10)
                        continue
                    else:
                        raise Exception(f"狀態查詢失敗: {status_response.status_code}")
                
                try:
                    status_data = status_response.json()
                except ValueError as e:
                    consecutive_failures += 1
                    logging.warning(f"狀態回應格式錯誤: {e}")
                    if consecutive_failures >= max_consecutive_failures:
                        raise Exception("伺服器回應格式持續錯誤")
                    time.sleep(5)
                    continue
                
                # 重置連續失敗計數
                consecutive_failures = 0
                
                status = status_data.get("status", "unknown")
                
                if status == "done":
                    video_url = status_data.get("video_url")
                    if not video_url:
                        raise Exception("任務完成但沒有影片網址")
                    return video_url
                elif status == "failed":
                    error_msg = status_data.get("error", "未知錯誤")
                    raise Exception(f"Vidu 處理失敗: {error_msg}")
                elif status in ["processing", "queued", "pending"]:
                    logging.info(f"任務狀態: {status} (第 {attempt + 1} 次檢查)")
                    time.sleep(2)
                else:
                    logging.warning(f"未知狀態: {status}")
                    time.sleep(2)
                    
            except (requests.exceptions.ConnectionError, 
                    requests.exceptions.Timeout) as e:
                consecutive_failures += 1
                logging.warning(f"網路錯誤 (第{attempt+1}次): {e}")
                
                if consecutive_failures >= max_consecutive_failures:
                    raise Exception("網路連線持續失敗，請檢查網路狀況")
                
                # 漸進式延遲
                delay = min(10 + consecutive_failures * 2, 30)
                time.sleep(delay)
                continue
                
            except Exception as e:
                if "任務完成但沒有影片網址" in str(e) or "Vidu 處理失敗" in str(e):
                    raise
                consecutive_failures += 1
                logging.warning(f"輪詢錯誤 (第{attempt+1}次): {e}")
                if consecutive_failures >= max_consecutive_failures:
                    raise
                time.sleep(5)
        
        raise TimeoutError(f"Vidu 處理超時 (已等待 {max_attempts * 2} 秒)")
    
    def download_file(self, url: str, dst: str) -> None:
        """下載檔案（帶重試機制）"""
        logging.info(f"下載影片: {url}")
        
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        
        max_retries = 3
        for retry_count in range(max_retries):
            try:
                # 使用 session 並設定適當的 headers
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "AI-Booth/1.0"
                })
                
                response = session.get(
                    url, 
                    stream=True, 
                    timeout=(10, 300)  # 10秒連線，5分鐘讀取
                )
                response.raise_for_status()
                
                # 檢查檔案大小
                content_length = response.headers.get('content-length')
                if content_length:
                    expected_size = int(content_length)
                    logging.info(f"預期檔案大小: {expected_size / 1024 / 1024:.1f} MB")
                
                # 下載檔案
                downloaded_size = 0
                with open(dst, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                
                # 驗證下載完整性
                if content_length and downloaded_size != expected_size:
                    raise Exception(f"檔案下載不完整: {downloaded_size}/{expected_size} bytes")
                
                logging.info(f"檔案下載完成: {downloaded_size / 1024 / 1024:.1f} MB")
                return
                
            except (requests.exceptions.ConnectionError, 
                    requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError) as e:
                logging.warning(f"下載失敗 (第{retry_count + 1}次): {e}")
                if retry_count < max_retries - 1:
                    import time
                    time.sleep(2 ** retry_count)  # 指數退避
                    continue
                else:
                    raise Exception(f"下載失敗，已重試 {max_retries} 次: {str(e)}")
            except Exception as e:
                logging.error(f"下載錯誤: {e}")
                raise
    
    def render_ae(self, src_mp4: str, serial: str) -> str:
        """After Effects 渲染"""
        ae_exe = self.config.get('ae_exe')
        ae_project = self.config.get('ae_project')
        output_dir = self.config.get('output_dir')
        
        if not os.path.exists(ae_exe):
            raise FileNotFoundError(f"After Effects 執行檔不存在: {ae_exe}")
        
        if not os.path.exists(ae_project):
            raise FileNotFoundError(f"AE 專案檔不存在: {ae_project}")
        
        os.makedirs(output_dir, exist_ok=True)
        out_mp4 = os.path.join(output_dir, f"{serial}_final.mp4")
        
        cmd = [
            ae_exe, "-project", ae_project,
            "-comp", "OUT",
            "-RStemplate", "AI_H264",
            "-output", out_mp4,
            "-s", "0", "-e", "0", "-mp", "-v", "ERRORS"
        ]
        
        logging.info(f"執行 AE: {' '.join(cmd)}")
        
        env = os.environ.copy()
        env["AI_SRC"] = src_mp4
        
        try:
            subprocess.run(cmd, env=env, check=True, timeout=300)
            return out_mp4
        except subprocess.TimeoutExpired:
            raise TimeoutError("After Effects 渲染超時")
        except subprocess.CalledProcessError as e:
            raise Exception(f"After Effects 渲染失敗: {e}")

class EmailSender:
    """郵件發送類"""
    
    def __init__(self, config):
        self.config = config
        self.SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    
    def get_gmail_service(self):
        """取得 Gmail 服務"""
        creds = None
        client_secret_path = Path(__file__).parent / "client_secret.json"
        
        if not client_secret_path.exists():
            raise FileNotFoundError("找不到 client_secret.json 檔案")
        
        token_file = "token.json"
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, self.SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logging.error(f"Token 刷新失敗: {e}")
                    creds = None
            
            if not creds:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(client_secret_path), self.SCOPES
                )
                creds = flow.run_local_server(port=0)
            
            with open(token_file, "w") as token:
                token.write(creds.to_json())
        
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    
    def send_email(self, to_addr: str, video_path: str) -> bool:
        """發送郵件"""
        try:
            service = self.get_gmail_service()
            
            msg = EmailMessage()
            msg["Subject"] = "AI 動動棚影片已完成"
            msg["From"] = self.config.get('gmail_from')
            msg["To"] = to_addr
            msg.set_content("您的 AI 動動棚影片已完成！請查看附件中的 MP4 檔案。")
            
            if not os.path.exists(video_path):
                raise FileNotFoundError(f"影片檔案不存在: {video_path}")
            
            with open(video_path, "rb") as f:
                video_data = f.read()
            
            filename = os.path.basename(video_path)
            msg.add_attachment(
                video_data, 
                maintype="video", 
                subtype="mp4", 
                filename=filename
            )
            
            raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(
                userId="me", 
                body={"raw": raw_message}
            ).execute()
            
            return True
            
        except Exception as e:
            logging.exception("郵件發送失敗")
            raise

class AIBoothApp(tk.Tk):
    """主應用程式類"""
    
    def __init__(self):
        super().__init__()
        
        # 初始化配置和服務
        self.config = Config()
        self.video_processor = VideoProcessor(self.config)
        self.email_sender = EmailSender(self.config)
        
        # 設定日誌
        self.setup_logging()
        
        # 初始化變數
        self.job_queue = queue.Queue()
        self.current_job = None
        self.selected_path = None
        self.final_mp4 = None
        self.tkvideo_player = None
        
        # 建立 UI
        self.setup_ui()
        
        # 啟動工作線程
        self.start_worker_thread()
        
        # 確保輸出目錄存在
        os.makedirs(self.config.get('output_dir'), exist_ok=True)
    
    def setup_logging(self):
        """設定日誌"""
        logging.basicConfig(
            filename="ai_booth.log",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding='utf-8'
        )
    
    def setup_ui(self):
        """建立使用者介面"""
        self.title("AI 動動棚影片產生工具")
        self.geometry("900x700")
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        
        # 左側面板
        self.create_left_panel()
        
        # 右側面板
        self.create_right_panel()
        
        # 綁定事件
        self.bind("<<JobDone>>", self.on_job_done)
        self.bind("<<JobFail>>", self.on_job_fail)
    
    def create_left_panel(self):
        """建立左側面板"""
        left_frame = tk.Frame(self)
        left_frame.grid(row=0, column=0, sticky="n", padx=12, pady=12)
        
        # 檔案選擇區
        file_frame = tk.Frame(left_frame)
        file_frame.pack(anchor="w")
        
        self.btn_pick = ttk.Button(
            file_frame, 
            text="選擇圖片", 
            command=self.pick_file
        )
        self.btn_pick.pack(side="left")
        
        self.lbl_path = tk.Label(
            file_frame, 
            text="未選擇檔案", 
            width=38, 
            anchor="w"
        )
        self.lbl_path.pack(side="left", padx=6)
        
        # API 按鈕
        self.btn_api = ttk.Button(
            left_frame, 
            text="產生影片（Vidu+AE）", 
            command=self.run_api, 
            state="disabled"
        )
        self.btn_api.pack(anchor="w", pady=8)
        
        # 影片預覽區
        self.preview_label = tk.Label(
            left_frame, 
            text="影片預覽", 
            font=("Arial", 13, "bold")
        )
        self.preview_label.pack(anchor="w", pady=(8, 2))
        
        self.video_label = tk.Label(
            left_frame, 
            width=48, 
            height=27, 
            bg="#222"
        )
        self.video_label.pack(pady=12, anchor="w")
        
        # 進度條
        self.pbar = ttk.Progressbar(
            left_frame, 
            length=340, 
            mode="determinate"
        )
        self.pbar.pack(pady=2, anchor="w")
        
        self.status_msg = tk.Label(left_frame, text="", fg="blue")
        self.status_msg.pack(anchor="w")
    
    def create_right_panel(self):
        """建立右側面板"""
        right_frame = tk.Frame(self)
        right_frame.grid(row=0, column=1, sticky="n", padx=24, pady=40)
        
        # 郵件設定區
        mail_frame = tk.Frame(right_frame)
        mail_frame.pack()
        
        tk.Label(mail_frame, text="收件 Email:").pack(side="left")
        
        self.ent_mail = ttk.Entry(mail_frame, width=24, foreground="gray")
        self.ent_mail.pack(side="left", padx=6)
        
        self.mail_placeholder = "請輸入 Email"
        self.ent_mail.insert(0, self.mail_placeholder)
        self.ent_mail.bind("<FocusIn>", self.clear_placeholder)
        self.ent_mail.bind("<FocusOut>", self.restore_placeholder)
        
        self.btn_mail = ttk.Button(
            mail_frame, 
            text="📤 寄送影片", 
            command=self.mail_video, 
            state="disabled"
        )
        self.btn_mail.pack(side="left", padx=4)
        
        self.tip_label = tk.Label(
            right_frame, 
            text="⚠️ 需先產生影片，成功後才可寄送 Email", 
            fg="red"
        )
        self.tip_label.pack(pady=10)
        
        # 流程記錄區
        self.process_label = tk.Label(
            right_frame, 
            text="流程進度記錄", 
            font=("Arial", 13, "bold")
        )
        self.process_label.pack(anchor="w", pady=(6, 2))
        
        self.process_log = scrolledtext.ScrolledText(
            right_frame, 
            width=42, 
            height=12, 
            state="disabled", 
            font=("Consolas", 10)
        )
        self.process_log.pack(pady=10)
    
    def clear_placeholder(self, event):
        """清除佔位符"""
        if self.ent_mail.get() == self.mail_placeholder:
            self.ent_mail.delete(0, "end")
            self.ent_mail.config(foreground="black")
    
    def restore_placeholder(self, event):
        """恢復佔位符"""
        if not self.ent_mail.get():
            self.ent_mail.insert(0, self.mail_placeholder)
            self.ent_mail.config(foreground="gray")
    
    def log_process(self, msg: str, error: bool = False):
        """記錄流程訊息"""
        self.process_log.config(state="normal")
        
        if error:
            self.process_log.insert("end", f"⚠️ [錯誤] {msg}\n")
        else:
            self.process_log.insert("end", f"✔️ {msg}\n")
            
        self.process_log.see("end")
        self.process_log.config(state="disabled")
    
    def pick_file(self):
        """選擇檔案"""
        file_path = filedialog.askopenfilename(
            filetypes=[("圖片檔案", "*.jpg *.jpeg *.png")]
        )
        
        if not file_path:
            return
        
        self.selected_path = file_path
        self.lbl_path.config(text=file_path)
        self.btn_api["state"] = "normal"
        self.set_progress(0, "等待產生影片…")
        self.btn_mail["state"] = "disabled"
        self.tip_label.config(
            text="⚠️ 需先產生影片，成功後才可寄送 Email", 
            fg="red"
        )
        self.log_process(f"已選取檔案：{file_path}")
        
        # 更新郵件佔位符
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        self.mail_placeholder = f"請輸入 {base_name} 的 email"
        self.ent_mail.delete(0, "end")
        self.ent_mail.insert(0, self.mail_placeholder)
        self.ent_mail.config(foreground="gray")
    
    def run_api(self):
        """執行 API 任務"""
        if not self.selected_path:
            messagebox.showwarning("未選檔", "請先選擇圖片")
            return
        
        if self.current_job:
            messagebox.showwarning("忙碌中", "目前有任務執行中")
            return
        
        # 先進行網路檢查
        self.log_process("檢查網路連線…")
        self.set_progress(5, "檢查網路連線…")
        
        # 檢查基本網路連線
        is_connected, msg = NetworkChecker.check_internet_connection()
        if not is_connected:
            error_msg = f"網路連線檢查失敗: {msg}"
            self.log_process(error_msg, error=True)
            messagebox.showerror("網路錯誤", error_msg)
            self.set_progress(0, "網路檢查失敗")
            return
        
        # 檢查 Vidu API 連線
        vidu_key = self.config.get('vidu_key')
        if not vidu_key:
            error_msg = "Vidu API Key 未設定，請先設定 API Key"
            self.log_process(error_msg, error=True)
            messagebox.showerror("設定錯誤", error_msg)
            self.set_progress(0, "API Key 未設定")
            return
        
        api_ok, api_msg = NetworkChecker.check_vidu_api_access(vidu_key)
        if not api_ok:
            error_msg = f"Vidu API 檢查失敗: {api_msg}"
            self.log_process(error_msg, error=True)
            messagebox.showerror("API 錯誤", error_msg)
            self.set_progress(0, "API 檢查失敗")
            return
        
        self.log_process("網路連線檢查通過，啟動影片產生流程…")
        self.set_progress(10, "任務啟動…")
        self.job_queue.put({"image_path": self.selected_path})
        
        # 禁用按鈕
        self.btn_api["state"] = "disabled"
        self.btn_pick["state"] = "disabled"
    
    def set_progress(self, value: int, msg: str = ""):
        """設定進度"""
        self.pbar["value"] = value
        self.status_msg.config(text=msg)
    
    def get_video_filename(self, img_path: str) -> str:
        """產生影片檔名"""
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{base_name}_{timestamp}.mp4"
    
    def start_worker_thread(self):
        """啟動工作線程"""
        worker_thread = threading.Thread(
            target=self.worker, 
            daemon=True
        )
        worker_thread.start()
    
    def worker(self):
        """工作線程處理函數"""
        while True:
            try:
                job = self.job_queue.get()
                self.current_job = job["image_path"]
                
                # 產生時間戳
                serial = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                
                # 步驟1: Vidu 上傳
                self.log_process("開始 Vidu 上傳…")
                self.set_progress(20, "Vidu 上傳中…")
                
                try:
                    vidu_url = self.video_processor.call_vidu(job["image_path"])
                except requests.RequestException as e:
                    if "無法連線" in str(e) or "網路連線錯誤" in str(e):
                        raise Exception("網路連線失敗，請檢查網路設定並重試")
                    elif "API Key" in str(e):
                        raise Exception("API Key 錯誤，請檢查設定")
                    elif "請求次數過多" in str(e):
                        raise Exception("API 請求次數過多，請稍後再試")
                    else:
                        raise Exception(f"Vidu API 錯誤: {str(e)}")
                except ValueError as e:
                    raise Exception(f"設定錯誤: {str(e)}")
                except TimeoutError as e:
                    raise Exception("Vidu 處理超時，請稍後重試")
                except Exception as e:
                    if "網路連線持續失敗" in str(e):
                        raise Exception("網路不穩定，請檢查網路連線後重試")
                    else:
                        raise Exception(f"Vidu 處理失敗: {str(e)}")
                
                # 步驟2: 下載影片
                self.log_process("Vidu 上傳完成，開始下載影片…")
                tmp_mp4 = os.path.join(
                    self.config.get('output_dir'), 
                    f"{serial}_vidu.mp4"
                )
                self.set_progress(40, "影片下載中…")
                
                try:
                    self.video_processor.download_file(vidu_url, tmp_mp4)
                except Exception as e:
                    if "下載失敗" in str(e):
                        raise Exception("影片下載失敗，可能是網路問題或檔案損壞")
                    else:
                        raise Exception(f"下載錯誤: {str(e)}")
                
                # 步驟3: AE 處理
                self.log_process("影片下載完成，開始 AE 處理…")
                self.set_progress(70, "AE 處理中…")
                
                try:
                    final_mp4 = self.video_processor.render_ae(tmp_mp4, serial)
                except FileNotFoundError as e:
                    raise Exception(f"找不到必要檔案: {str(e)}")
                except TimeoutError as e:
                    raise Exception("After Effects 處理超時")
                except Exception as e:
                    raise Exception(f"After Effects 處理失敗: {str(e)}")
                
                self.log_process(f"AE 處理完成，影片路徑：{final_mp4}")
                self.set_progress(100, "已完成")
                
                # 通知完成
                self.event_generate("<<JobDone>>", when="tail", data=final_mp4)
                
            except Exception as e:
                error_msg = str(e)
                
                # 特殊處理 RetryError
                if "RetryError" in error_msg:
                    if "ConnectionError" in error_msg:
                        error_msg = "網路連線問題，請檢查網路設定後重試"
                    elif "Timeout" in error_msg:
                        error_msg = "連線超時，請檢查網路速度後重試"
                    else:
                        error_msg = "操作失敗，已多次重試仍無法完成，請稍後再試"
                
                self.log_process(error_msg, error=True)
                logging.exception("處理失敗")
                self.event_generate("<<JobFail>>", when="tail", data=error_msg)
            finally:
                self.current_job = None
                self.job_queue.task_done()
    
    def on_job_done(self, event):
        """任務完成處理"""
        src_video_path = event.data
        video_filename = self.get_video_filename(self.selected_path)
        
        # 複製到桌面
        desktop_dir = Path.home() / "Desktop" / "ai_studio"
        desktop_dir.mkdir(exist_ok=True)
        
        desktop_video_path = desktop_dir / video_filename
        shutil.copy2(src_video_path, desktop_video_path)
        
        self.final_mp4 = str(desktop_video_path)
        self.log_process(f"影片已複製到桌面：{desktop_video_path}")
        
        # 播放預覽
        try:
            if self.tkvideo_player:
                self.tkvideo_player = None
            
            self.tkvideo_player = tkvideo(
                str(desktop_video_path), 
                self.video_label, 
                loop=1, 
                size=(480, 270)
            )
            self.tkvideo_player.play()
        except Exception as e:
            self.log_process(f"影片預覽失敗：{e}", error=True)
        
        # 更新 UI
        self.btn_mail["state"] = "normal"
        self.tip_label.config(
            text="✅ 影片生成成功！請填寫 Email 並寄送", 
            fg="green"
        )
        self.btn_api["state"] = "normal"
        self.btn_pick["state"] = "normal"
        self.set_progress(100, "已完成！可預覽與寄信")
        self.log_process("影片生成並可預覽！")
    
    def on_job_fail(self, event):
        """任務失敗處理"""
        error_msg = event.data
        messagebox.showerror("處理失敗", f"處理失敗：{error_msg}")
        
        self.btn_mail["state"] = "disabled"
        self.tip_label.config(text="⚠️ 產生影片失敗，請重試", fg="red")
        self.btn_api["state"] = "normal"
        self.btn_pick["state"] = "normal"
        self.set_progress(0, "任務失敗")
        self.log_process(error_msg, error=True)
    
    def mail_video(self):
        """寄送影片"""
        email = self.ent_mail.get().strip()
        
        if not email or email == self.mail_placeholder:
            messagebox.showwarning("缺少 Email", "請輸入收件者 Email")
            return
        
        if not self.final_mp4:
            messagebox.showwarning("無影片", "請先產生影片")
            return
        
        try:
            self.log_process(f"開始寄送 Email 給 {email}…")
            self.email_sender.send_email(email, self.final_mp4)
            self.log_process("Email 已成功寄出！")
            messagebox.showinfo("完成", "Email 已成功寄出！")
        except Exception as e:
            error_msg = str(e)
            self.log_process(f"寄信失敗：{error_msg}", error=True)
            logging.exception("寄信錯誤")
            messagebox.showerror("寄信錯誤", error_msg)

def main():
    """主程式入口"""
    app = AIBoothApp()
    app.mainloop()

if __name__ == "__main__":
    main()