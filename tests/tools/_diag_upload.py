"""快速诊断 bilibili upload 卡住在哪一步。"""
import asyncio, json, os, sys, subprocess
sys.path.insert(0, '.')

print("step 1: import bilibili_api...", flush=True)
from bilibili_api.login_v2 import Credential
from bilibili_api import video_uploader
from bilibili_api.video_uploader import VideoUploader, VideoMeta, Lines
print("step 1 OK", flush=True)

print("step 2: load credential...", flush=True)
with open('./cache/bili_cookies.json', encoding='utf-8') as f:
    data = json.load(f)
cred = Credential(
    sessdata      = data['sessdata'],
    bili_jct      = data['bili_jct'],
    buvid3        = data['buvid3'],
    dedeuserid    = data.get('dedeuserid', ''),
    ac_time_value = data.get('ac_time_value', ''),
)
print("step 2 OK", flush=True)

async def main():
    print("step 3: check_refresh...", flush=True)
    try:
        need = await asyncio.wait_for(cred.check_refresh(), timeout=10)
        print(f"step 3 OK: need_refresh={need}", flush=True)
    except asyncio.TimeoutError:
        print("step 3 TIMEOUT", flush=True)
    except Exception as e:
        print(f"step 3 ERROR: {e}", flush=True)

    import glob
    videos = glob.glob('./output/**/*.mp4', recursive=True)
    if not videos:
        print("没有找到 output 视频，退出", flush=True)
        return
    video_path = videos[0]
    print(f"step 4: 准备封面 {video_path}", flush=True)

    cover_path = video_path + '.cover.jpg'
    r = subprocess.run(['ffmpeg', '-y', '-ss', '3', '-i', video_path,
                        '-vframes', '1', '-q:v', '2', cover_path],
                       capture_output=True, timeout=30)
    print(f"  ffmpeg returncode={r.returncode}, cover exists={os.path.exists(cover_path)}", flush=True)
    if not os.path.exists(cover_path):
        print(f"  ffmpeg stderr: {r.stderr[-300:]}", flush=True)
        return
    print("step 4 OK", flush=True)

    print("step 5: 构建 VideoMeta...", flush=True)
    meta = VideoMeta(
        title    = 'diag-test',
        desc     = 'diag test',
        tid      = 254,
        tags     = ['test'],
        cover    = cover_path,
        original = False,
        source   = 'https://www.youtube.com/',
    )
    print("step 5 OK", flush=True)

    uploader = VideoUploader(
        pages=[video_uploader.VideoUploaderPage(path=video_path, title='diag-test')],
        meta=meta,
        credential=cred,
        line=Lines.WS,
    )

    @uploader.on('__ALL__')
    async def on_event(data):
        print(f"  event: {data.get('name')} {data.get('data', {})}", flush=True)

    print("step 6: uploader.start() (timeout=120s)...", flush=True)
    try:
        result = await asyncio.wait_for(uploader.start(), timeout=120)
        print(f"step 6 OK: {result}", flush=True)
    except asyncio.TimeoutError:
        print("step 6 TIMEOUT (120s内无响应)", flush=True)
    except Exception as e:
        print(f"step 6 ERROR: {type(e).__name__}: {e}", flush=True)

asyncio.run(main())
