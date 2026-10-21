import subprocess
import re
import sys
import yt_dlp

PROXY = "socks5h://127.0.0.1:40000"
COOKIES_PATH = "/root/Bot-Download/cookies.txt"
USER_AGENT = "Mozilla/5.0 (Android 16; Mobile; rv:159.0) Gecko/159.0 Firefox/159.0"

def test_ytdlp(url):
    print("\n--- Testando 1: yt-dlp ---")
    ydl_opts = {
        'proxy': PROXY,
        'cookiefile': COOKIES_PATH,
        'user_agent': USER_AGENT,
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream = info.get('url') or (info['formats'][-1]['url'] if 'formats' in info else None)
            if stream:
                print(f"[yt-dlp SUCESSO] Stream encontrada: {stream[:80]}...")
                return stream
    except Exception as e:
        print(f"[yt-dlp ERRO] {e}")
    return None

def test_gallery_dl(url):
    print("\n--- Testando 2: gallery-dl ---")
    cmd = [
        "/root/Bot-Download/venv/bin/gallery-dl",
        "--cookies", COOKIES_PATH,
        "--proxy", PROXY,
        "-g",
        url
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        output = res.stdout.strip()
        if res.returncode == 0 and output:
            stream = output.splitlines()[0]
            print(f"[gallery-dl SUCESSO] Stream encontrada: {stream[:80]}...")
            return stream
        else:
            print(f"[gallery-dl ERRO] Código {res.returncode}. STDERR: {res.stderr.strip()[:120]}")
    except Exception as e:
        print(f"[gallery-dl EXCEÇÃO] {e}")
    return None

def test_curl_regex(url):
    print("\n--- Testando 3: curl + regex ---")
    cmd = [
        "curl", "-s", "-L",
        "-x", PROXY,
        "-A", USER_AGENT,
        "-b", COOKIES_PATH,
        url
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        html = res.stdout
        m3u8 = re.search(r'https://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
        if m3u8:
            print(f"[curl regex SUCESSO - M3U8]: {m3u8.group(0)[:80]}...")
            return m3u8.group(0)
        mp4 = re.search(r'https://[^\s"\'<>]+\.mp4[^\s"\'<>]*', html)
        if mp4:
            print(f"[curl regex SUCESSO - MP4]: {mp4.group(0)[:80]}...")
            return mp4.group(0)
        print("[curl regex FALHA] Nenhuma URL de mídia detectada no HTML.")
    except Exception as e:
        print(f"[curl regex EXCEÇÃO] {e}")
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python test_extract.py <URL>")
        sys.exit(1)
    
    target_url = sys.argv[1]
    print(f"Alvo: {target_url}")
    
    res = test_ytdlp(target_url) or test_gallery_dl(target_url) or test_curl_regex(target_url)
    
    if res:
        print("\n=> RESULTADO FINAL: Mídia capturada com sucesso!")
    else:
        print("\n=> RESULTADO FINAL: Todos os métodos falharam.")
