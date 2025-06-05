import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from tkvideo import tkvideo
import threading, queue, requests, subprocess, os, logging, datetime
from tenacity import retry, wait_exponential, stop_after_attempt
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import time
# === 全局設定 ===
VIDU_KEY    = "YOUR_VIDU_TOKEN"
AE_EXE      = r"C:\Program Files\Adobe\...\aerender.exe"    # 請改為你的路徑
AE_PROJECT  = r"C:\AI_Booth\template.aep"
AE_SCRIPT   = r"C:\AI_Booth\swap.jsx"
OUTPUT_DIR  = r"C:\AI_Booth\output"
LOG_FILE    = "ai_booth.log"

logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

job_q = queue.Queue()      # 背景任務佇列
current_job = None         # 避免同時進行多任務

# === Vidu API 工具函式 ===
@retry(wait=wait_exponential(multiplier=2), stop=stop_after_attempt(3))
def call_vidu(image_path: str) -> str:
    """將 JPEG 上傳到 Vidu，回傳影片下載網址"""
    logging.info("上傳至 Vidu: %s", image_path)
    resp = requests.post(
        "https://api.vidu.ai/jobs",
        headers={"Authorization": f"Bearer {VIDU_KEY}"},
        files={"image": open(image_path, "rb")}
    ).json()
    job_id = resp["id"]
    while True:
        stat = requests.get(f"https://api.vidu.ai/jobs/{job_id}",
                            headers={"Authorization": f"Bearer {VIDU_KEY}"}).json()
        if stat["status"] == "done":
            return stat["video_url"]
        time.sleep(2)

def download_file(url: str, dst: str):
    """下載檔案並存到 dst"""
    logging.info("下載影片 %s", url)
    r = requests.get(url, stream=True)
    with open(dst, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

def render_ae(src_mp4: str, serial: str) -> str:
    """呼叫 After Effects 腳本，產生最終影片"""
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

def send_email(to_addr: str, video_path: str):
    """用 Gmail API 寄信，附影片檔案路徑或下載連結"""
    creds = Credentials.from_authorized_user_file("token.json",
              ["https://www.googleapis.com/auth/gmail.send"])
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = "AI 動動棚影片已完成"
    msg["From"] = "you@gmail.com"
    msg["To"] = to_addr
    msg.set_content(f"嗨！你的影片已完成。\n本地路徑：{video_path}\n請手動上傳雲端後再轉寄給對方。")
    import base64
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

# === 背景工作 ===
def worker(app):
    global current_job
    while True:
        image_path = job_q.get()
        current_job = image_path
        try:
            serial = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            # 1. 串 Vidu API
            vidu_url = call_vidu(image_path)
            tmp_mp4  = os.path.join(OUTPUT_DIR, f"{serial}_vidu.mp4")
            download_file(vidu_url, tmp_mp4)
            # 2. AE 渲染
            final_mp4 = render_ae(tmp_mp4, serial)
            # 3. 回主執行緒載入影片
            app.event_generate("<<JobDone>>", when="tail", data=final_mp4)
        except Exception as e:
            logging.exception("處理失敗")
            app.event_generate("<<JobFail>>", when="tail", data=str(e))
        finally:
            current_job = None
            job_q.task_done()

# === GUI 主程式 ===
class App(tk.Tk):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title("AI 動動棚影片產生工具")
        self.geometry("800x600")

        # 影片顯示區
        self.video_label = tk.Label(self, width=800, height=450, bg="#222")
        self.video_label.pack(padx=10, pady=15, fill="both", expand=True)

        # 檔案選擇區
        file_frame = tk.Frame(self)
        file_frame.pack(pady=8)
        self.btn_pick = ttk.Button(file_frame, text="選取照片 (JPG)", command=self.pick_file)
        self.btn_pick.pack(side="left")
        self.lbl_path = tk.Label(file_frame, text="尚未選擇檔案")
        self.lbl_path.pack(side="left", padx=6)

        # 進度條
        self.pbar = ttk.Progressbar(self, length=650, mode="determinate")
        self.pbar.pack(pady=6)

        # Email 欄位
        mail_frame = tk.Frame(self)
        mail_frame.pack(pady=10)
        tk.Label(mail_frame, text="收件 Email:").pack(side="left")
        self.ent_mail = ttk.Entry(mail_frame, width=30)
        self.ent_mail.pack(side="left", padx=6)
        self.btn_mail = ttk.Button(mail_frame, text="📤 寄送影片", command=self.mail_video, state="disabled")
        self.btn_mail.pack(side="left")

        # 狀態綁定
        self.bind("<<JobDone>>", self.on_done)
        self.bind("<<JobFail>>", self.on_fail)

        self.final_mp4 = None
        self.tkvideo_player = None

        # OUTPUT_DIR 檢查
        if not os.path.isdir(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

    def pick_file(self):
        """選擇圖片，投入任務佇列"""
        global current_job
        if current_job:
            messagebox.showwarning("忙碌中", "請等待目前影片產生完畢")
            return
        f = filedialog.askopenfilename(filetypes=[("JPEG", "*.jpg;*.jpeg")])
        if not f:
            return
        self.lbl_path.config(text=f)
        self.pbar["value"] = 0
        job_q.put(f)
        self.btn_pick["state"] = "disabled"
        self.btn_mail["state"] = "disabled"
        self.pbar["value"] = 10

    def on_done(self, evt):
        """背景線程完成，載入影片並開啟寄信按鈕"""
        video_path = evt.data
        self.final_mp4 = video_path
        # 預覽影片
        if self.tkvideo_player:
            self.tkvideo_player = None  # 前一個 thread 會自動結束
        self.tkvideo_player = tkvideo(video_path, self.video_label, loop=1, size=(800, 450))
        self.tkvideo_player.play()
        self.btn_mail["state"] = "normal"
        self.btn_pick["state"] = "normal"
        self.pbar["value"] = 100

    def on_fail(self, evt):
        messagebox.showerror("處理失敗", f"處理失敗：{evt.data}")
        self.btn_pick["state"] = "normal"
        self.pbar["value"] = 0

    def mail_video(self):
        """寄信並提示成功/失敗"""
        email = self.ent_mail.get().strip()
        if not email:
            messagebox.showwarning("缺少 Email", "請輸入收件者 Email")
            return
        video_path = self.final_mp4  # 預設直接寄送本地路徑
        try:
            send_email(email, video_path)
            messagebox.showinfo("完成", "已寄出！")
        except Exception as e:
            logging.exception("寄信錯誤")
            messagebox.showerror("寄信錯誤", str(e))

# === 啟動 ===
if __name__ == "__main__":
    app = App()
    threading.Thread(target=worker, args=(app,), daemon=True).start()
    app.mainloop()
