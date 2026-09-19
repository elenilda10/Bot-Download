import os
import re
import json
import subprocess

COOKIES_PATH = "/root/Bot-Download/cookies.txt"

def clean_instagram_url(url: str) -> str:
    # Remove parâmetros desnecessários da URL (?utm_source, etc.)
    return url.split("?")[0].strip()

def extract_instagram_media(url: str):
    clean_url = clean_instagram_url(url)
    results = []

    # 1. Tentativa com gallery-dl (rápida, max 15s)
    print(f"[gallery-dl] Testando: {clean_url}")
    try:
        cmd_gallery = [
            "gallery-dl",
            "--cookies", COOKIES_PATH,
            "-j",
            clean_url
        ]
        proc = subprocess.run(cmd_gallery, capture_output=True, text=True, timeout=15)
        
        if proc.returncode == 0 and proc.stdout.strip():
            for line in proc.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, list) and len(data) >= 2:
                        media_url = data[1]
                        if isinstance(media_url, str) and media_url.startswith("http"):
                            results.append({"url": media_url, "source": "gallery-dl"})
                    elif isinstance(data, dict):
                        m_url = data.get("url") or data.get("video_url") or data.get("display_url")
                        if m_url:
                            results.append({"url": m_url, "source": "gallery-dl"})
                except json.JSONDecodeError:
                    continue

        if results:
            print(f"[Sucesso] Extraído via gallery-dl ({len(results)} mídia(s)).")
            return results

    except subprocess.TimeoutExpired:
        print("[Aviso] gallery-dl deu timeout (15s).")
    except Exception as e:
        print(f"[Aviso] gallery-dl falhou: {e}")

    # 2. Fallback com yt-dlp (com timeout de 15s)
    print("[yt-dlp] Tentando fallback...")
    try:
        cmd_ytdlp = [
            "yt-dlp",
            "--cookies", COOKIES_PATH,
            "-J",
            "--no-warnings",
            "--no-playlist",
            clean_url
        ]
        proc = subprocess.run(cmd_ytdlp, capture_output=True, text=True, timeout=15)
        
        if proc.returncode == 0 and proc.stdout.strip():
            info = json.loads(proc.stdout)
            
            if "entries" in info and info["entries"]:
                for entry in info["entries"]:
                    if entry and entry.get("url"):
                        results.append({"url": entry["url"], "source": "yt-dlp"})
            elif info.get("url"):
                results.append({"url": info["url"], "source": "yt-dlp"})

        if results:
            print(f"[Sucesso] Extraído via yt-dlp ({len(results)} mídia(s)).")
            return results
        else:
            print("[yt-dlp] Nenhuma mídia encontrada na saída.")

    except subprocess.TimeoutExpired:
        print("[Aviso] yt-dlp deu timeout (15s).")
    except Exception as e:
        print(f"[Erro] yt-dlp falhou: {e}")

    return []

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        medias = extract_instagram_media(test_url)
        for i, m in enumerate(medias, 1):
            print(f"{i}. [{m['source']}] {m['url']}")
        if not medias:
            print("Falha na extração em ambos os métodos.")
    else:
        print("Uso: python3 insta_downloader.py <URL>")
