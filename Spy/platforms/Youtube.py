import asyncio
import glob
import os
import random
import re
from typing import Union

import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from googleapiclient.discovery import build 
from googleapiclient.errors import HttpError

import config
from Spy import LOGGER
from Spy.utils.formatters import time_to_seconds

logger = LOGGER(__name__)

# --- API KEY & CONFIG ---
def get_api_keys():
    key = getattr(config, "API_KEY", getattr(config, "YT_API_KEY", getattr(config, "API_KEYS", None)))
    if isinstance(key, list): return key
    if isinstance(key, str): return [k.strip() for k in key.split(",")]
    return []

API_KEYS = get_api_keys()
current_key_index = 0
PROXY = getattr(config, "PROXY_URL", None)

def get_youtube_client():
    global current_key_index
    if not API_KEYS or current_key_index >= len(API_KEYS):
        return None
    try:
        return build("youtube", "v3", developerKey=API_KEYS[current_key_index], static_discovery=False)
    except Exception:
        return None

def switch_key():
    global current_key_index
    current_key_index += 1
    return current_key_index < len(API_KEYS)

def get_cookie_file():
    try:
        folder_path = os.path.join(os.getcwd(), "cookies")
        txt_files = glob.glob(os.path.join(folder_path, '*.txt'))
        return random.choice(txt_files) if txt_files else None
    except:
        return None

class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be|youtube\.com\/shorts)"

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message: Message) -> Union[str, None]:
        messages = [message, message.reply_to_message] if message.reply_to_message else [message]
        for msg in messages:
            if msg.entities:
                for e in msg.entities:
                    if e.type == MessageEntityType.URL:
                        return (msg.text or msg.caption)[e.offset : e.offset + e.length]
            if msg.caption_entities:
                for e in msg.caption_entities:
                    if e.type == MessageEntityType.TEXT_LINK:
                        return e.url
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        # Extract Video ID
        if videoid: 
            vidid = link
        else:
            match = re.search(r"(?:v=|\/|shorts\/|youtu\.be\/)([0-9A-Za-z_-]{11})", link)
            vidid = match.group(1) if match else None

        # 1. Try with Google API First
        youtube = get_youtube_client()
        if youtube:
            try:
                if not vidid:
                    search = await asyncio.to_thread(youtube.search().list(q=link, part="id", maxResults=1, type="video").execute)
                    if search.get("items"): vidid = search["items"][0]["id"]["videoId"]
                
                if vidid:
                    v_data = await asyncio.to_thread(youtube.videos().list(part="snippet,contentDetails", id=vidid).execute)
                    if v_data.get("items"):
                        item = v_data["items"][0]
                        title = item["snippet"]["title"]
                        thumb = item["snippet"]["thumbnails"]["high"]["url"]
                        duration = item["contentDetails"]["duration"]
                        # Manual duration parse
                        it = re.finditer(r'\d+[HMS]', duration)
                        d_min = ":".join([x.group()[:-1].zfill(2) for x in it])
                        return title, d_min, 0, thumb, vidid
            except HttpError as e:
                if e.resp.status == 403 and switch_key(): return await self.details(link, videoid)

        # 2. Advanced Fallback (If API fails, use yt-dlp)
        try:
            cookie = get_cookie_file()
            ydl_opts = {
                "quiet": True, "cookiefile": cookie, "proxy": PROXY,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_query = f"ytsearch1:{link}" if not vidid else self.base + vidid
                info = await asyncio.to_thread(ydl.extract_info, search_query, download=False)
                if 'entries' in info: info = info['entries'][0]
                
                title = info.get("title")
                vidid = info.get("id")
                thumb = info.get("thumbnail")
                duration = info.get("duration", 0)
                m, s = divmod(duration, 60)
                h, m = divmod(m, 60)
                d_min = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
                return title, d_min, duration, thumb, vidid
        except Exception as e:
            logger.error(f"Everything failed for {link}: {e}")
            return None

    async def track(self, link: str, videoid: Union[bool, str] = None):
        res = await self.details(link, videoid)
        if not res: return None, None
        title, d_min, d_sec, thumb, vidid = res
        return {"title": title, "link": self.base + vidid, "vidid": vidid, "duration_min": d_min, "thumb": thumb}, vidid

    async def download(self, link: str, mystic, video=None, videoid=None, songaudio=None, songvideo=None, format_id=None, title=None) -> str:
        if videoid: link = self.base + link
        loop = asyncio.get_running_loop()
        cookie = get_cookie_file()
        if not os.path.exists("downloads"): os.mkdir("downloads")

        common_opts = {
            "quiet": True, "proxy": PROXY, "cookiefile": cookie, "geo_bypass": True,
            "outtmpl": "downloads/%(title)s.%(ext)s",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        def ytdl_run(opts):
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(link, download=True)
                return ydl.prepare_filename(info)

        try:
            if songvideo: opts = {**common_opts, "format": f"{format_id}+140/bestvideo+bestaudio", "merge_output_format": "mp4"}
            elif songaudio: opts = {**common_opts, "format": "bestaudio/best", "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]}
            else: opts = {**common_opts, "format": "bestaudio/best"}
            return await loop.run_in_executor(None, lambda: ytdl_run(opts))
        except Exception as e:
            logger.error(f"Download error: {e}")
            return None
