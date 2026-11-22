import os
import json
import math
import asyncio
import subprocess
import tempfile
import shutil
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import edge_tts
from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")

WIDTH, HEIGHT = 1080, 1920  # YouTube Shorts (9:16)
FPS = 30
VOICE = "en-US-ChristopherNeural"  # Engaging storytelling male voice
HISTORY_FILE = "history.json"


# --- 1. GENERATE STORYBOARD VIA GEMINI ---
def generate_storyboard():
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    past_topics = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            past_topics = json.load(f)

    prompt = f"""
    Create a viral, punchy 35-45 second Stickman Animation YouTube Short script about human psychology, money habits, or clever life lessons.
    Avoid these already covered topics: {past_topics[-15:]}

    Return ONLY valid JSON with this exact schema:
    {{
      "topic": "Short topic summary",
      "title": "Catchy YouTube Title with #Shorts",
      "description": "Engaging 2-line description with hashtags",
      "tags": ["stickman", "animation", "psychology", "shorts", "storytime"],
      "scenes": [
        {{
          "narration": "1-2 short spoken sentences for this scene.",
          "pose": "idle | pointing | thinking | celebrating | shocked",
          "emotion": "neutral | happy | angry | shocked",
          "badge_text": "2-3 WORD KEY PHRASE"
        }}
      ]
    }}
    Include 5 to 7 scenes total.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)

    past_topics.append(data["topic"])
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(past_topics, f, indent=2)

    return data


# --- 2. GENERATE AUDIO PER SCENE (EDGE-TTS) ---
async def synthesize_scene_audio(text, out_mp3):
    communicate = edge_tts.Communicate(text, VOICE, rate="+8%")
    await communicate.save(out_mp3)


def get_audio_duration(filepath):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", filepath
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return float(result.stdout.strip())


# --- 3. STICKMAN PROCEDURAL FRAME RENDERER ---
def load_font(size):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "arialbd.ttf"
    ]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


FONT_BADGE = load_font(62)
FONT_SUB = load_font(48)


def draw_wrapped_text(draw, text, font, max_width, center_x, start_y, fill=(20, 20, 20)):
    words = text.split()
    lines, current = [], ""
    for w in words:
        test = f"{current} {w}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)

    y = start_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        draw.text((center_x - w // 2, y), line, font=font, fill=fill)
        y += (bbox[3] - bbox[1]) + 14


def render_stickman_frame(frame_idx, total_frames, scene):
    # Clean off-white paper canvas
    img = Image.new("RGB", (WIDTH, HEIGHT), (248, 249, 252))
    draw = ImageDraw.Draw(img)

    # Subtle grid background
    for gx in range(0, WIDTH, 90):
        draw.line([(gx, 0), (gx, HEIGHT)], fill=(235, 238, 245), width=2)
    for gy in range(0, HEIGHT, 90):
        draw.line([(0, gy), (WIDTH, gy)], fill=(235, 238, 245), width=2)

    # Top Keyword Badge Card
    badge = scene.get("badge_text", "").upper()
    if badge:
        draw.rounded_rectangle([140, 240, WIDTH - 140, 380], radius=30, fill=(255, 224, 71), outline=(20, 20, 20), width=6)
        draw_wrapped_text(draw, badge, FONT_BADGE, WIDTH - 320, WIDTH // 2, 275, fill=(20, 20, 20))

    # Ground line
    ground_y = 1280
    draw.line([(120, ground_y), (WIDTH - 120, ground_y)], fill=(40, 40, 40), width=8)

    # Smooth breathing / bounce motion
    t = frame_idx / FPS
    bob = int(math.sin(t * 6) * 10)
    cx = WIDTH // 2
    hip_y = ground_y - 240 + bob
    neck_y = hip_y - 230
    head_r = 95
    head_cy = neck_y - head_r
    stroke = 16
    ink = (25, 28, 36)

    # Legs
    draw.line([(cx, hip_y), (cx - 75, ground_y)], fill=ink, width=stroke)
    draw.line([(cx, hip_y), (cx + 75, ground_y)], fill=ink, width=stroke)

    # Torso
    draw.line([(cx, neck_y), (cx, hip_y)], fill=ink, width=stroke)

    # Arms based on Pose
    pose = scene.get("pose", "idle")
    shoulder_y = neck_y + 45
    wave = int(math.sin(t * 10) * 20)

    if pose == "pointing":
        draw.line([(cx, shoulder_y), (cx - 110, shoulder_y + 110)], fill=ink, width=stroke)
        draw.line([(cx, shoulder_y), (cx + 190, shoulder_y - 40 + wave)], fill=ink, width=stroke)
    elif pose == "thinking":
        draw.line([(cx, shoulder_y), (cx - 90, shoulder_y + 130)], fill=ink, width=stroke)
        draw.line([(cx, shoulder_y), (cx + 110, shoulder_y + 40), (cx + 55, head_cy + 75)], fill=ink, width=stroke)
    elif pose == "celebrating":
        draw.line([(cx, shoulder_y), (cx - 150, shoulder_y - 140 + wave)], fill=ink, width=stroke)
        draw.line([(cx, shoulder_y), (cx + 150, shoulder_y - 140 - wave)], fill=ink, width=stroke)
    elif pose == "shocked":
        draw.line([(cx, shoulder_y), (cx - 135, head_cy + 10)], fill=ink, width=stroke)
        draw.line([(cx, shoulder_y), (cx + 135, head_cy + 10)], fill=ink, width=stroke)
    else:  # idle / explaining
        draw.line([(cx, shoulder_y), (cx - 120, shoulder_y + 90 + wave)], fill=ink, width=stroke)
        draw.line([(cx, shoulder_y), (cx + 120, shoulder_y + 90 - wave)], fill=ink, width=stroke)

    # Head Circle
    draw.ellipse(
        [cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
        fill=(255, 255, 255), outline=ink, width=stroke
    )

    # Eyes (blink every ~3 seconds)
    is_blinking = (frame_idx % 90) in (0, 1, 2)
    eye_y = head_cy - 15
    if is_blinking:
        draw.line([(cx - 45, eye_y), (cx - 20, eye_y)], fill=ink, width=8)
        draw.line([(cx + 20, eye_y), (cx + 45, eye_y)], fill=ink, width=8)
    else:
        draw.ellipse([cx - 42, eye_y - 10, cx - 22, eye_y + 10], fill=ink)
        draw.ellipse([cx + 22, eye_y - 10, cx + 42, eye_y + 10], fill=ink)

    # Eyebrows based on emotion
    emotion = scene.get("emotion", "neutral")
    if emotion == "angry":
        draw.line([(cx - 50, eye_y - 28), (cx - 18, eye_y - 16)], fill=ink, width=7)
        draw.line([(cx + 50, eye_y - 28), (cx + 18, eye_y - 16)], fill=ink, width=7)
    elif emotion == "shocked":
        draw.arc([cx - 50, eye_y - 36, cx - 16, eye_y - 16], 180, 360, fill=ink, width=6)
        draw.arc([cx + 16, eye_y - 36, cx + 50, eye_y - 16], 180, 360, fill=ink, width=6)

    # Talking Mouth (lip-sync oscillation)
    mouth_y = head_cy + 35
    talk_open = abs(math.sin(t * 18)) > 0.45
    if emotion == "shocked":
        draw.ellipse([cx - 18, mouth_y - 18, cx + 18, mouth_y + 22], outline=ink, width=8)
    elif talk_open:
        draw.ellipse([cx - 26, mouth_y - 12, cx + 26, mouth_y + 18], fill=ink)
    else:
        draw.arc([cx - 28, mouth_y - 10, cx + 28, mouth_y + 15], 0, 180, fill=ink, width=8)

    # Subtitle Box at Bottom
    draw.rounded_rectangle([80, 1380, WIDTH - 80, 1660], radius=28, fill=(255, 255, 255), outline=(210, 215, 225), width=4)
    draw_wrapped_text(draw, scene["narration"], FONT_SUB, WIDTH - 220, WIDTH // 2, 1425, fill=(30, 35, 45))

    return img


# --- 4. ASSEMBLE FULL VIDEO ---
async def build_video(storyboard, output_mp4):
    tmp_dir = tempfile.mkdtemp()
    segment_files = []

    try:
        for idx, scene in enumerate(storyboard["scenes"]):
            audio_path = os.path.join(tmp_dir, f"scene_{idx}.mp3")
            await synthesize_scene_audio(scene["narration"], audio_path)
            duration = get_audio_duration(audio_path) + 0.25

            frames_dir = os.path.join(tmp_dir, f"frames_{idx}")
            os.makedirs(frames_dir, exist_ok=True)
            total_frames = max(1, int(duration * FPS))

            for f_i in range(total_frames):
                frame_img = render_stickman_frame(f_i, total_frames, scene)
                frame_img.save(os.path.join(frames_dir, f"frame_{f_i:05d}.png"))

            seg_mp4 = os.path.join(tmp_dir, f"seg_{idx}.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-framerate", str(FPS),
                "-i", os.path.join(frames_dir, "frame_%05d.png"),
                "-i", audio_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", seg_mp4
            ], check=True)
            segment_files.append(seg_mp4)

        # Concatenate all scenes
        concat_txt = os.path.join(tmp_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for seg in segment_files:
                f.write(f"file '{seg}'\n")

        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_txt, "-c", "copy", output_mp4
        ], check=True)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --- 5. UPLOAD TO YOUTUBE ---
def upload_to_youtube(video_path, storyboard):
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": storyboard["title"][:100],
            "description": storyboard["description"],
            "tags": storyboard["tags"],
            "categoryId": "24"  # Entertainment
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"Uploaded successfully! Video ID: {response['id']}")


async def main():
    print("1. Generating stickman storyboard...")
    storyboard = generate_storyboard()
    print(f"Topic: {storyboard['topic']}")

    output_mp4 = "final_stickman_short.mp4"
    print("2. Rendering stickman animation & audio...")
    await build_video(storyboard, output_mp4)

    print("3. Uploading to YouTube...")
    upload_to_youtube(output_mp4, storyboard)


if __name__ == "__main__":
    asyncio.run(main())
