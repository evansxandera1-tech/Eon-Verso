#!/usr/bin/env python3
"""Publica una sola vez ROMA en YouTube EON-Verso a partir de partes en Drive."""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CHANNEL_ID = "UCQI8q5L9aHk8BvznYj9hxsg"
DRIVE_DIR = "EON-Verso/Publicacion_Roma"
PART_COUNT = 14
VIDEO_SIZE = 229718869
VIDEO_SHA256 = "d43013773e576bdb5fed7f3b988a59618cbf1469ccbdcb59882822bac6ad45a5"
STATE_FILE = "eon-roma-publication-state.json"
TITLE = "ROMA: El imperio que dominó el mundo"
DESCRIPTION = (
    "Roma construyó uno de los imperios más poderosos de la historia. "
    "En este documental de EON-Verso exploramos el mundo de sus emperadores, "
    "legiones, conquistas y conflictos, tomando la historia que inspiró Gladiador "
    "como punto de partida.\n\n"
    "De Marco Aurelio a Cómodo: ¿qué ocurrió realmente en el Imperio romano?\n\n"
    "Una narración histórica de EON-Verso.\n\n"
    "#Roma #ImperioRomano #Historia #Gladiador #EONVerso"
)
TAGS = ["Roma", "Imperio romano", "historia de Roma", "Cómodo", "Marco Aurelio",
        "Gladiador", "Gladiator", "historia universal", "EON-Verso"]

def env(name):
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError("Falta configurar " + name)
    return val

def youtube():
    credentials = Credentials(
        token=None, refresh_token=env("YOUTUBE_REFRESH_TOKEN"),
        client_id=env("YOUTUBE_CLIENT_ID"),
        client_secret=env("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token")
    yt = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    channels = yt.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if len(channels) != 1 or channels[0]["id"] != CHANNEL_ID:
        raise RuntimeError("Canal incorrecto: solo se permite EON-Verso.")
    print("Canal EON-Verso verificado", flush=True)
    return yt

def state_read():
    url = "https://api.github.com/repos/" + env("GITHUB_REPOSITORY") + "/contents/" + STATE_FILE
    headers = {"Authorization": "Bearer " + env("GH_TOKEN"), "Accept": "application/vnd.github+json"}
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        return {}, None
    response.raise_for_status()
    data = response.json()
    return json.loads(base64.b64decode(data["content"])), data["sha"]

def state_write(data, sha):
    url = "https://api.github.com/repos/" + env("GITHUB_REPOSITORY") + "/contents/" + STATE_FILE
    headers = {"Authorization": "Bearer " + env("GH_TOKEN"), "Accept": "application/vnd.github+json"}
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    body = {"message": "Registrar publicación única ROMA en EON-Verso",
            "content": base64.b64encode(content.encode()).decode()}
    if sha:
        body["sha"] = sha
    response = requests.put(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    return response.json()["content"]["sha"]

def reconstruct(work):
    output = work / "ROMA_1080P_SOLO_VOZ.mp4"
    digest = hashlib.sha256()
    size = 0
    with output.open("wb") as target:
        for n in range(PART_COUNT):
            filename = f"roma_1080p.part{n:02d}"
            part = work / filename
            subprocess.run(["rclone", "copyto", "gdrive:" + DRIVE_DIR + "/" + filename, str(part)],
                           check=True, timeout=1200)
            expected = 16 * 1024 * 1024 if n < PART_COUNT - 1 else VIDEO_SIZE - (PART_COUNT - 1) * 16 * 1024 * 1024
            if not part.is_file() or part.stat().st_size != expected:
                raise RuntimeError("Parte inválida: " + filename)
            with part.open("rb") as stream:
                while chunk := stream.read(4 * 1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
                    size += len(chunk)
            part.unlink()
            print(f"ROMA parte {n+1}/{PART_COUNT} verificada", flush=True)
    if size != VIDEO_SIZE or digest.hexdigest() != VIDEO_SHA256:
        raise RuntimeError("ROMA no coincide con el SHA256 original; no publicar.")
    probe = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height", "-of", "json", str(output)], text=True)
    info = json.loads(probe)
    duration = float(info["format"]["duration"])
    streams = info["streams"]
    if not (765 <= duration <= 775 and
            any(s.get("codec_type") == "video" and s.get("width") == 1920 and s.get("height") == 1080 for s in streams) and
            any(s.get("codec_type") == "audio" for s in streams)):
        raise RuntimeError("ROMA: duración, video o audio incorrectos.")
    print(f"MP4 verificado: {duration:.2f}s, SHA256 {digest.hexdigest()}", flush=True)
    return output

def thumbnail(work):
    dest = work / "ROMA_COMODO_1280x720.png"
    subprocess.run(["rclone", "copyto", "gdrive:" + DRIVE_DIR + "/" + dest.name, str(dest)],
                   check=True, timeout=300)
    if not dest.is_file() or not (10000 < dest.stat().st_size < 2000000):
        raise RuntimeError("Miniatura de Cómodo no disponible o demasiado grande.")
    return dest

def existing_video(yt):
    # Evita duplicados si la subida terminó pero el registro de estado falló.
    response = yt.search().list(part="snippet", forMine=True, type="video",
                                q=TITLE, maxResults=50).execute()
    for item in response.get("items", []):
        if item["snippet"]["title"].strip().casefold() == TITLE.casefold():
            return item["id"]["videoId"]
    return None

def upload(yt, path):
    req = yt.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": TITLE, "description": DESCRIPTION, "tags": TAGS,
                           "categoryId": "27", "defaultLanguage": "es"},
              "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                         "containsSyntheticMedia": True}},
        media_body=MediaFileUpload(str(path), mimetype="video/mp4",
                                   chunksize=8*1024*1024, resumable=True))
    result = None
    while result is None:
        progress, result = req.next_chunk()
        if progress:
            print("YouTube %.0f%%" % (progress.progress() * 100), flush=True)
    return result["id"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["verify", "publish"], default="verify")
    args = parser.parse_args()
    yt = youtube()
    state, sha = state_read()
    if state.get("youtube_id") and state.get("thumbnail_done"):
        print("YA PUBLICADO: https://www.youtube.com/watch?v=" + state["youtube_id"], flush=True)
        return
    with tempfile.TemporaryDirectory(prefix="eon_roma_") as folder:
        work = Path(folder)
        thumb = thumbnail(work)
        if args.mode == "verify":
            reconstruct(work)
            print("VERIFICACIÓN OK: no se publicó.", flush=True)
            return
        if not state.get("youtube_id"):
            found = existing_video(yt)
            if found:
                print("Video ya existente: " + found, flush=True)
                state["youtube_id"] = found
            else:
                video = reconstruct(work)
                state["youtube_id"] = upload(yt, video)
            state["source"] = DRIVE_DIR + " (SHA256 " + VIDEO_SHA256 + ")"
            sha = state_write(state, sha)
        if not state.get("thumbnail_done"):
            yt.thumbnails().set(videoId=state["youtube_id"],
                media_body=MediaFileUpload(str(thumb), mimetype="image/png")).execute()
            state["thumbnail_done"] = True
            sha = state_write(state, sha)
        print("PUBLICADO: https://www.youtube.com/watch?v=" + state["youtube_id"], flush=True)

if __name__ == "__main__":
    main()
