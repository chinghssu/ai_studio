import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkvideo import tkvideo
import threading, queue, requests, subprocess, os, logging, datetime, base64
from tenacity import retry, wait_exponential, stop_after_attempt
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.message import EmailMessage
from google_auth_oauthlib.flow import InstalledAppFlow
from tkinter import scrolledtext
import shutil
import datetime

# ======= 全局設定 =======
VIDU_KEY    = "vda_828095877196292096_TdlooPGW57Pjwt3VlQId05nA0SqI76bu"  # Vidu API 金鑰
AE_EXE      = r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe"  # AE 執行檔路徑
AE_PROJECT  = r"C:\AI_Booth\template.aep"      # AE 專案檔
AE_SCRIPT   = r"C:\AI_Booth\swap.jsx"          # AE Script (未用到)
OUTPUT_DIR  = r"C:\AI_Booth\output"            # 輸出資料夾
LOG_FILE    = "ai_booth.log"                   # Log 檔案
SCOPES      = ["https://www.googleapis.com/auth/gmail.send"]  # Gmail 權限

# 設定 logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

job_q = queue.Queue()      # 任務佇列
current_job = None         # 目前進行中的任務

# 取得影片檔名（根據圖片檔名與時間戳）
def get_video_filename(img_path):
    base = os.path.splitext(os.path.basename(img_path))[0]  # 例如 IMG_1234
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{base}_{ts}.mp4"

# ======= Gmail API 函數 =======
def get_gmail_service():
    # 自動從 token.json 認證，無則引導一次 OAuth 登入
    creds = None
    CLIENT_SECRET_PATH = os.path.join(os.path.dirname(__file__), "client_secret.json")
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service

def send_email(to_addr, video_path):
    # 寄送 mp4 附件到指定 Email
    try:
        service = get_gmail_service()
        msg = EmailMessage()
        msg["Subject"] = "AI 動動棚影片已完成"
        msg["From"] = "你的 gmail 帳號"
        msg["To"] = to_addr
        msg.set_content("AI 動動棚影片已完成，mp4 檔案已作為附件寄出！")
        # 讀取並附加 mp4
        with open(video_path, "rb") as f:
            mp4_data = f.read()
        msg.add_attachment(mp4_data, maintype="video", subtype="mp4", filename=os.path.basename(video_path))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True
    except Exception as e:
        logging.exception("寄信錯誤")
        raise

# ======= 檔案操作函數 =======
def copy_to_desktop(src_mp4):
    # 複製影片到桌面 ai_studio 資料夾
    desktop_dir = os.path.expanduser("~/Desktop/ai_studio/")
    if not os.path.isdir(desktop_dir):
        os.makedirs(desktop_dir)
    dst_mp4 = os.path.join(desktop_dir, os.path.basename(src_mp4))
    shutil.copy2(src_mp4, dst_mp4)
    return dst_mp4

def clear_placeholder(self, event):
    if self.ent_mail.get() == self.mail_placeholder:
        self.ent_mail.delete(0, "end")
        self.ent_mail.config(foreground="black")

def restore_placeholder(self, event):
    if not self.ent_mail.get():
        self.ent_mail.insert(0, self.mail_placeholder)
        self.ent_mail.config(foreground="gray")

def pick_file(self):
    f = filedialog.askopenfilename(filetypes=[("JPEG", "*.jpg;*.jpeg")])
    if not f:
        return
    self.selected_path = f
    self.lbl_path.config(text=f)
    self.btn_api["state"] = "normal"
    self.set_progress(0, "等待產生影片…")
    self.btn_mail["state"] = "disabled"
    self.tip_label.config(text="⚠️ 需先產生影片，成功後才可寄送 Email", fg="red")
    self.log_process(f"已選取檔案：{f}")

    # 提取檔名（不含副檔名）
    base = os.path.splitext(os.path.basename(f))[0]
    self.mail_placeholder = f"請輸入 {base} 的 email"
    self.ent_mail.delete(0, "end")
    self.ent_mail.insert(0, self.mail_placeholder)
    self.ent_mail.config(foreground="gray")

# ======= Vidu & AE API 任務 =======
@retry(wait=wait_exponential(multiplier=2), stop=stop_after_attempt(3))
def call_vidu(image_path: str) -> str:
    # 上傳圖片到 Vidu，輪詢直到影片產生完成，回傳影片網址
    logging.info("上傳至 Vidu: %s", image_path)
    resp = requests.post(
        "https://api.vidu.ai/jobs",
        headers={"Authorization": f"Bearer {VIDU_KEY}"},
        files={"image": open(image_path, "rb")}
    ).json()
    job_id = resp["id"]
    import time
    while True:
        stat = requests.get(f"https://api.vidu.ai/jobs/{job_id}",
                            headers={"Authorization": f"Bearer {VIDU_KEY}"}).json()
        if stat["status"] == "done":
            return stat["video_url"]
        time.sleep(2)

def download_file(url: str, dst: str):
    # 下載影片檔案
    logging.info("下載影片 %s", url)
    r = requests.get(url, stream=True)
    with open(dst, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

def render_ae(src_mp4: str, serial: str) -> str:
    # 呼叫 After Effects 進行影片合成
    out_mp4 = os.path.join(OUTPUT_DIR, f"{serial}_final.mp4")
    cmd = [AE_EXE, "-project", AE_PROJECT,
           "-comp", "OUT",
           "-RStemplate", "AI_H264",
           "-output", out_mp4,
           "-s", "0", "-e", "0", "-mp", "-v", "ERRORS"]
    logging.info("執行 AE: %s", " ".join(cmd))
    env = os.environ.copy()
    env["AI_SRC"] = src_mp4
    subprocess.check_call(cmd, env=env)
    return out_mp4

# ======= 背景工作執行緒 =======
def worker(app):
    # 處理任務佇列，依序執行 Vidu 上傳、下載、AE 合成
    global current_job
    while True:
        job = job_q.get()
        current_job = job["image_path"]
        try:
            serial = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            app.log_process("開始 Vidu 上傳…")
            app.set_progress(20, "Vidu 上傳中…")
            vidu_url = call_vidu(job["image_path"])
            app.log_process("Vidu 上傳完成，開始下載影片…")
            tmp_mp4  = os.path.join(OUTPUT_DIR, f"{serial}_vidu.mp4")
            app.set_progress(40, "影片下載中…")
            download_file(vidu_url, tmp_mp4)
            app.log_process("影片下載完成，開始 AE 處理…")
            app.set_progress(70, "AE 處理中…")
            final_mp4 = render_ae(tmp_mp4, serial)
            app.log_process(f"AE 處理完成，影片路徑：{final_mp4}")
            app.set_progress(100, "已完成")
            app.event_generate("<<JobDone>>", when="tail", data=final_mp4)
        except Exception as e:
            app.log_process(f"{e}", error=True)
            logging.exception("處理失敗")
            app.event_generate("<<JobFail>>", when="tail", data=str(e))
        finally:
            current_job = None
            job_q.task_done()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI 動動棚影片產生工具")
        self.geometry("980x420")
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        # ========== 左側欄 ==========
        left_frame = tk.Frame(self)
        left_frame.grid(row=0, column=0, sticky="n", padx=12, pady=12)

        file_frame = tk.Frame(left_frame)
        file_frame.pack(anchor="w")
        self.btn_pick = ttk.Button(file_frame, text="選擇圖片", command=self.pick_file)
        self.btn_pick.pack(side="left")
        self.lbl_path = tk.Label(file_frame, text="未選擇檔案", width=38, anchor="w")
        self.lbl_path.pack(side="left", padx=6)

        self.btn_api = ttk.Button(left_frame, text="產生影片（Vidu+AE）", command=self.run_api, state="disabled")
        self.btn_api.pack(anchor="w", pady=8)

        # ========== 影片預覽區 ==========
        self.preview_label = tk.Label(left_frame, text="影片預覽", font=("Arial", 13, "bold"))
        self.preview_label.pack(anchor="w", pady=(8,2))
        self.video_label = tk.Label(left_frame, width=48, height=27, bg="#222")
        self.video_label.pack(pady=12, anchor="w")

        self.pbar = ttk.Progressbar(left_frame, length=340, mode="determinate")
        self.pbar.pack(pady=2, anchor="w")
        self.status_msg = tk.Label(left_frame, text="", fg="blue")
        self.status_msg.pack(anchor="w")

        # ========== 右側欄 ==========
        right_frame = tk.Frame(self)
        right_frame.grid(row=0, column=1, sticky="n", padx=24, pady=40)

        mail_frame = tk.Frame(right_frame)
        mail_frame.pack()
        tk.Label(mail_frame, text="收件 Email:").pack(side="left")
        self.ent_mail = ttk.Entry(mail_frame, width=24, foreground="gray")
        self.ent_mail.pack(side="left", padx=6)
        self.mail_placeholder = "請輸入 Email"
        self.ent_mail.insert(0, self.mail_placeholder)
        self.ent_mail.bind("<FocusIn>", self.clear_placeholder)
        self.ent_mail.bind("<FocusOut>", self.restore_placeholder)
        self.btn_mail = ttk.Button(mail_frame, text="📤 寄送影片", command=self.mail_video, state="disabled")
        self.btn_mail.pack(side="left", padx=4)
        self.tip_label = tk.Label(right_frame, text="⚠️ 需先產生影片，成功後才可寄送 Email", fg="red")
        self.tip_label.pack(pady=10)

        # ========== 新增流程 Process Log ==========
        # 右側（流程紀錄區塊）
        self.process_label = tk.Label(right_frame, text="流程進度記錄", font=("Arial", 13, "bold"))
        self.process_label.pack(anchor="w", pady=(6,2))
        self.process_log = scrolledtext.ScrolledText(right_frame, width=42, height=12, state="disabled", font=("Consolas", 10))
        self.process_log.pack(pady=10)

        self.final_mp4 = None
        self.tkvideo_player = None

        self.bind("<<JobDone>>", self.on_done)
        self.bind("<<JobFail>>", self.on_fail)

        if not os.path.isdir(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

    # -------- Process Log --------
    def log_process(self, msg, error=False):
        # 新增流程紀錄到右側區塊
        self.process_log.config(state="normal")
        if error:
            self.process_log.insert("end", f"⚠️ [錯誤] {msg}\n")
        else:
            self.process_log.insert("end", f"✔️ {msg}\n")
        self.process_log.see("end")
        self.process_log.config(state="disabled")

    # -------- File select --------
    def pick_file(self):
        # 選擇圖片檔案
        f = filedialog.askopenfilename(filetypes=[("JPEG", "*.jpg;*.jpeg")])
        if not f:
            return
        self.selected_path = f
        self.lbl_path.config(text=f)
        self.btn_api["state"] = "normal"
        self.set_progress(0, "等待產生影片…")
        self.btn_mail["state"] = "disabled"
        self.tip_label.config(text="⚠️ 需先產生影片，成功後才可寄送 Email", fg="red")
        self.log_process(f"已選取檔案：{f}")

    # -------- Run API --------
    def run_api(self):
        # 啟動影片產生流程
        global current_job
        if not hasattr(self, "selected_path") or not self.selected_path:
            messagebox.showwarning("未選檔", "請先選擇圖片")
            return
        if current_job:
            messagebox.showwarning("忙碌中", "目前有任務執行中")
            return
        self.log_process("啟動影片產生流程…")
        self.set_progress(10, "任務啟動…")
        job_q.put({"image_path": self.selected_path})
        self.btn_api["state"] = "disabled"
        self.btn_pick["state"] = "disabled"

    def set_progress(self, value, msg=""):
        # 設定進度條與狀態訊息
        self.pbar["value"] = value
        self.status_msg.config(text=msg)

    # -------- 產生完成/失敗 --------
    def on_done(self, evt):
        # 任務完成時呼叫，複製影片到桌面並預覽
        src_video_path = evt.data
        video_filename = get_video_filename(self.selected_path)
        desktop_dir = os.path.expanduser("~/Desktop/ai_studio/")
        if not os.path.isdir(desktop_dir):
            os.makedirs(desktop_dir)
        desktop_video_path = os.path.join(desktop_dir, video_filename)
        shutil.copy2(src_video_path, desktop_video_path)
        self.final_mp4 = desktop_video_path
        self.log_process(f"影片已複製到桌面：{desktop_video_path}")

        # 預覽影片
        if self.tkvideo_player:
            self.tkvideo_player = None
        self.tkvideo_player = tkvideo(desktop_video_path, self.video_label, loop=1, size=(480, 270))
        self.tkvideo_player.play()
        self.btn_mail["state"] = "normal"
        self.tip_label.config(text="✅ 影片生成成功！請填寫 Email 並寄送", fg="green")
        self.btn_api["state"] = "normal"
        self.btn_pick["state"] = "normal"
        self.set_progress(100, "已完成！可預覽與寄信")
        self.log_process("影片生成並可預覽！")

    def on_fail(self, evt):
        # 任務失敗時呼叫
        messagebox.showerror("處理失敗", f"處理失敗：{evt.data}")
        self.btn_mail["state"] = "disabled"
        self.tip_label.config(text="⚠️ 產生影片失敗，請重試", fg="red")
        self.btn_api["state"] = "normal"
        self.btn_pick["state"] = "normal"
        self.set_progress(0, "任務失敗")
        self.log_process(f"{evt.data}", error=True)

    # -------- Mail --------
    def mail_video(self):
        # 寄送影片到指定 Email
        email = self.ent_mail.get().strip()
        if not email:
            messagebox.showwarning("缺少 Email", "請輸入收件者 Email")
            return
        video_path = self.final_mp4
        try:
            self.log_process(f"開始寄送 Email 給 {email}…")
            send_email(email, video_path)
            self.log_process("Email 已成功寄出！")
            messagebox.showinfo("完成", "已寄出！")
        except Exception as e:
            self.log_process(f"寄信失敗：{e}", error=True)
            logging.exception("寄信錯誤")
            messagebox.showerror("寄信錯誤", str(e))

# --- main ---
if __name__ == "__main__":
    app = App()
    threading.Thread(target=worker, args=(app,), daemon=True).start()
    app.mainloop()