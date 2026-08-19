import streamlit as st
import google.generativeai as genai
import tempfile
import time
import os
import re
from datetime import timedelta

# 設定網頁基本資訊
st.set_page_config(page_title="Gemini 影片字幕精準對齊系統", layout="wide")

# ================= 工具函式 =================

def snap_to_fps(ms, fps):
    """將毫秒根據指定的影片格率進行邊界對齊"""
    if fps is None:
        return ms
    frame_duration_ms = 1000.0 / fps
    snapped_ms = round(ms / frame_duration_ms) * frame_duration_ms
    return int(snapped_ms)

def adjust_srt_framerate(srt_text, fps):
    """處理整個 SRT 文本，校正時間碼格率"""
    if fps is None:
        return srt_text
    
    # 尋找所有 SRT 時間碼行，例如: 00:00:01,123 --> 00:00:03,456
    pattern = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})')
    
    def replace_timecode(match):
        h, m, s, ms = map(int, match.groups())
        total_ms = (h * 3600000) + (m * 60000) + (s * 1000) + ms
        snapped_ms = snap_to_fps(total_ms, fps)
        
        # 轉換回 HH:MM:SS,mmm
        delta = timedelta(milliseconds=snapped_ms)
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        milliseconds = delta.microseconds // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
    
    # 分別對 Start 與 End timecode 進行替換
    lines = srt_text.split('\n')
    adjusted_lines = []
    for line in lines:
        if '-->' in line:
            parts = line.split('-->')
            start_tc = pattern.sub(replace_timecode, parts[0])
            end_tc = pattern.sub(replace_timecode, parts[1])
            adjusted_lines.append(f"{start_tc.strip()} --> {end_tc.strip()}")
        else:
            adjusted_lines.append(line)
            
    return '\n'.join(adjusted_lines)

def srt_to_vtt(srt_text):
    """將 SRT 轉換為 VTT 以供網頁播放器預覽"""
    vtt = "WEBVTT\n\n"
    # 將逗號替換為點 (SRT 格式為 , mmm，VTT 格式為 . mmm)
    vtt += re.sub(r'(\d{2}:\d{2}:\d{2}),(\d{3})', r'\1.\2', srt_text)
    return vtt

# ================= AI 處理邏輯 =================

def process_alignment(api_key, media_path, transcript_text, mime_type):
    genai.configure(api_key=api_key)
    
    # System Instruction 優化
    system_instruction = """
    [角色任務]：你是一位專精於影片後製與字幕對齊的 AI 系統，具備絕對的音準與時間感知能力。
    [背景資訊]：使用者提供了一段多媒體檔案以及該檔案的「100% 原始逐字稿」。
    [具體指令]：
    1. 聆聽媒體內容，嚴格依照發音瞬間為逐字稿加上標準 SRT 時間碼。
    2. 毫秒級精確度與零延遲：在人聲出現的「瞬間」精確標記 Start Time，嚴格排除前導靜音。
    3. 3秒靜音校正機制：當偵測到語音中斷超過 3 秒以上時，下一句字幕的 Start Time 必須設定在「人聲重新開始的確切時間點」，絕對禁止接續上一條字幕的 End Time。
    [約束條件]：
    - 輸出必須為標準 SRT 格式（HH:MM:SS,mmm）。
    - 必須維持逐字稿 100% 原樣，禁止任何自行修改、精簡、擴充或刪減。
    """
    
    # 註：此處以官方最新支援多模態的 Pro 模型名稱為準。若未來有 gemini-3-pro-preview 則直接替換字串
    model = genai.GenerativeModel(
        model_name='gemini-3.6-flash',
        system_instruction=system_instruction
    )
    
    with st.spinner("🚀 正在上傳媒體檔案至 Google 伺服器..."):
        # 處理過大音訊/影片的上傳機制
        uploaded_file = genai.upload_file(path=media_path, mime_type=mime_type)
        
        # 輪詢檢查檔案處理狀態
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = genai.get_file(uploaded_file.name)
            
        if uploaded_file.state.name == "FAILED":
            st.error("檔案處理失敗，請檢查檔案格式或大小。")
            return None

    with st.spinner("🧠 Gemini 正在進行毫秒級對齊運算..."):
        prompt = f"請將這份逐字稿與上傳的媒體對齊，並嚴格遵循 System Instruction 的要求。逐字稿內容：\n\n{transcript_text}"
        response = model.generate_content([uploaded_file, prompt])
        
        # 為了安全起見，運算後從伺服器刪除暫存媒體檔
        genai.delete_file(uploaded_file.name)
        
    return response.text

# ================= UI 介面佈局 =================

st.title("🎬 Gemini 多模態字幕對齊系統")
st.markdown("上傳音訊 (`.aac`) 或小尺寸影片 (`.mp4`) 與逐字稿，透過 AI 嚴格對齊時間碼，並支援編輯預覽與格率轉換。")

with st.sidebar:
    st.header("⚙️ 系統設定")
    api_key = st.text_input("輸入 Google Gemini API Key", type="password")
    st.markdown("---")
    fps_option = st.selectbox(
        "🎬 影片對齊格率 (FPS)", 
        options=["不校正 (None)", "23.976", "24", "30"]
    )

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. 上傳媒體檔案 (< 20MB)")
    media_file = st.file_uploader("支援的格式: AAC, MP4", type=["aac", "mp4"])
    
with col2:
    st.subheader("2. 上傳純文字逐字稿 (.txt)")
    txt_file = st.file_uploader("上傳逐字稿 (TXT 格式)", type=["txt"])

if 'srt_output' not in st.session_state:
    st.session_state.srt_output = ""

if st.button("開始對齊作業", type="primary"):
    if not api_key:
        st.warning("請先於左側輸入 API Key")
    elif not media_file or not txt_file:
        st.warning("請確保媒體檔與逐字稿均已上傳")
    elif media_file.size > 20 * 1024 * 1024:
        st.error("媒體檔案超過 20MB 限制，請壓縮後重試。")
    else:
        # 建立暫存檔給 SDK 讀取
        mime_type = "video/mp4" if media_file.name.endswith('.mp4') else "audio/aac"
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{media_file.name.split('.')[-1]}") as tmp_media:
            tmp_media.write(media_file.read())
            tmp_media_path = tmp_media.name
            
        transcript = txt_file.read().decode("utf-8")
        
        # 執行核心對齊邏輯
        try:
            srt_result = process_alignment(api_key, tmp_media_path, transcript, mime_type)
            if srt_result:
                # 處理格率校正
                fps_val = None if fps_option == "不校正 (None)" else float(fps_option)
                final_srt = adjust_srt_framerate(srt_result, fps_val)
                st.session_state.srt_output = final_srt
                st.success("✅ 對齊完成！請在下方預覽或編輯。")
        except Exception as e:
            st.error(f"發生錯誤: {str(e)}")
        finally:
            os.remove(tmp_media_path)

# ================= 編輯器與影片預覽 =================

if st.session_state.srt_output:
    st.markdown("---")
    edit_col, preview_col = st.columns([1, 1])
    
    with edit_col:
        st.subheader("✏️ SRT 編輯器")
        # 提供即時修改的文字框，並回寫 session state 以更新預覽
        edited_srt = st.text_area(
            "您可以在此直接微調時間碼或字幕內容", 
            value=st.session_state.srt_output, 
            height=400
        )
        st.session_state.srt_output = edited_srt
        
        st.download_button(
            label="⬇️ 下載標準 SRT 檔案",
            data=edited_srt,
            file_name="aligned_subtitles.srt",
            mime="text/plain"
        )
        
    with preview_col:
        st.subheader("📺 播放與即時預覽")
        if media_file:
            # 將游標移回開頭以便播放
            media_file.seek(0)
            
            # 使用 Streamlit 原生字幕支援 (需轉為 VTT 格式)
            vtt_subtitles = srt_to_vtt(edited_srt)
            
            if media_file.name.endswith('.mp4'):
                # 播放影片並結合字幕
                st.video(media_file, subtitles={"繁體中文": vtt_subtitles})
            else:
                # 若為純音訊，仍可播放音訊本身供核對
                st.audio(media_file)
                st.info("💡 目前上傳的是音訊檔案。如需查看畫面字幕疊加效果，請上傳 MP4 影片。")