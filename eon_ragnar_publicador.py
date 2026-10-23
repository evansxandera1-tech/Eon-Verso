#!/usr/bin/env python3
"""Publicador dedicado Ragnar -> YouTube EON-Verso + Facebook Ojo al Piojo.

Fuente canónica del montaje Pro: Google Drive, nunca sustituir por V2.
Usa los secretos ya existentes en el repositorio HSF/Eon-Verso.
"""
import argparse
import base64
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

YT_ID = "UCQI8q5L9aHk8BvznYj9hxsg"
FB_ID = "111079017290300"
FB_NAME = "Ojo al Piojo"
GRAPH = "v25.0"
ZIP_DRIVE_ID = "1FzYK8HKiLXZe3pAH_SSJCFduXhRcA3TA"
VIDEO_DRIVE_PATH = "EON-Verso/Publicacion_Ragnar/EON_VERSO_RAGNAR_REHECHO_PRO_720P_COMPLETO.mp4"
STATE_FILE = "eon-ragnar-publication-state.json"


def require(name):
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError("Falta configurar secreto: " + name)
    return val


def rclone_copy(args, output):
    subprocess.run(["rclone", *args, str(output)], check=True, timeout=1400)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Descarga de Drive vacia: " + output.name)


def verify_youtube():
    cred = Credentials(
        token=None, refresh_token=require("YOUTUBE_REFRESH_TOKEN"),
        client_id=require("YOUTUBE_CLIENT_ID"),
        client_secret=require("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token")
    client = build("youtube", "v3", credentials=cred, cache_discovery=False)
    channels = client.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if len(channels) != 1 or channels[0]["id"] != YT_ID:
        raise RuntimeError("Canal YouTube no es EON-Verso, cancelado.")
    print("CANAL YOUTUBE VERIFICADO EON-Verso", flush=True)
    return client


def verify_facebook():
    page_id = require("FACEBOOK_PAGE_ID")
    if page_id != FB_ID:
        raise RuntimeError("Página Facebook incorrecta, cancelado.")
    token = require("FACEBOOK_PAGE_TOKEN")
    resp = requests.get(
        f"https://graph.facebook.com/{GRAPH}/{FB_ID}",
        params={"fields": "id,name", "access_token": token},
        timeout=35)
    data = resp.json()
    if (not resp.ok or data.get("id") != FB_ID or
            data.get("name", "").strip().casefold() != FB_NAME.casefold()):
        raise RuntimeError("No se pudo confirmar pagina Ojo al Piojo.")
    print("PAGINA FACEBOOK VERIFICADA Ojo al Piojo", flush=True)
    return token


def gh_state():
    repo = require("GITHUB_REPOSITORY")
    api = f"https://api.github.com/repos/{repo}/contents/{STATE_FILE}"
    headers = {"Authorization": "Bearer " + require("GH_TOKEN"),
               "Accept": "application/vnd.github+json"}
    r = requests.get(api, headers=headers, timeout=30)
    if r.status_code == 404:
        return {}, None
    r.raise_for_status()
    obj = r.json()
    return json.loads(base64.b64decode(obj["content"])), obj["sha"]


def save_state(state, sha):
    repo = require("GITHUB_REPOSITORY")
    api = f"https://api.github.com/repos/{repo}/contents/{STATE_FILE}"
    headers = {"Authorization": "Bearer " + require("GH_TOKEN"),
               "Accept": "application/vnd.github+json"}
    payload = {"message": "Registrar publicacion Ragnar EON-Verso y Ojo al Piojo",
               "content": base64.b64encode(
                   (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode()
               ).decode()}
    if sha:
        payload["sha"] = sha
    r = requests.put(api, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["content"]["sha"]


def text_file(p):
    return p.read_text(encoding="utf-8-sig").strip()


def verify_movie(path):
    if path.stat().st_size < 20_000_000:
        raise RuntimeError("Video Pro demasiado pequeno: descarga incompleta.")
    raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)], text=True)
    info = json.loads(raw)
    dur = float(info["format"]["duration"])
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if not (855 <= dur <= 865 and video and audio):
        raise RuntimeError(f"Montaje incorrecto: duracion {dur:.2f}, audio/video faltante.")
    if video.get("width", 0) < 1280 or video.get("height", 0) < 720:
        raise RuntimeError("Video Pro menor que 720p.")
    print(f"VIDEO PRO OK: {dur:.2f}s, {video['width']}x{video['height']}", flush=True)


def upload_youtube(client, movie, title, description, tags):
    op = client.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": title, "description": description,
                           "tags": tags, "categoryId": "27",
                           "defaultLanguage": "es"},
              "status": {"privacyStatus": "public",
                         "selfDeclaredMadeForKids": False,
                         "containsSyntheticMedia": True}},
        media_body=MediaFileUpload(str(movie), mimetype="video/mp4",
                                   chunksize=8*1024*1024, resumable=True))
    done = None
    while done is None:
        progress, done = op.next_chunk()
        if progress:
            print("YOUTUBE {:.0f}%".format(progress.progress()*100), flush=True)
    return done["id"]


def upload_facebook(token, movie, title, description):
    with movie.open("rb") as stream:
        r = requests.post(
            f"https://graph-video.facebook.com/{GRAPH}/{FB_ID}/videos",
            data={"access_token": token, "title": title,
                  "description": description, "published": "true"},
            files={"source": (movie.name, stream, "video/mp4")},
            timeout=1400)
    data = r.json()
    if not r.ok or not data.get("id"):
        raise RuntimeError("Error de Facebook: " + str(data.get("error", {}))[:400])
    return data["id"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["verify", "publish"], default="verify")
    opts = p.parse_args()
    yt = verify_youtube()
    fb_token = verify_facebook()
    state, sha = gh_state()

    with tempfile.TemporaryDirectory(prefix="ragnar_pub_") as temp:
        work = Path(temp)
        pack = work / "pack.zip"
        rclone_copy(["backend", "copyid", "gdrive:", ZIP_DRIVE_ID], pack)
        with zipfile.ZipFile(pack) as z:
            z.extractall(work / "assets")
        assets = work / "assets"
        title = text_file(assets / "YOUTUBE_TITULO.txt")
        description = text_file(assets / "YOUTUBE_DESCRIPCION_CAPITULOS.txt")
        tags = [i.strip() for i in text_file(assets / "YOUTUBE_ETIQUETAS.txt").split(",")]
        fb_title = text_file(assets / "FACEBOOK_TITULO.txt")
        fb_text = text_file(assets / "FACEBOOK_PUBLICACION.txt")
        thumb = assets / "MINIATURA_RAGNAR_EXISTIO_1280x720.jpg"
        if not (thumb.is_file() and thumb.stat().st_size < 2_000_000):
            raise RuntimeError("Miniatura JPG invalida.")
        movie = work / Path(VIDEO_DRIVE_PATH).name
        print("Descargando montaje Pro canónico desde Drive...", flush=True)
        rclone_copy(["copyto", "gdrive:" + VIDEO_DRIVE_PATH], movie)
        verify_movie(movie)

        if opts.mode == "verify":
            print("VERIFICACION OK. NO SE PUBLICO.", flush=True)
            return

        if "youtube_id" not in state:
            state["youtube_id"] = upload_youtube(yt, movie, title, description, tags)
            state["source"] = VIDEO_DRIVE_PATH
            sha = save_state(state, sha)
        if not state.get("thumbnail_done"):
            yt.thumbnails().set(
                videoId=state["youtube_id"],
                media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")).execute()
            state["thumbnail_done"] = True
            sha = save_state(state, sha)
        if "facebook_id" not in state:
            state["facebook_id"] = upload_facebook(fb_token, movie, fb_title, fb_text)
            sha = save_state(state, sha)
        print("YOUTUBE https://www.youtube.com/watch?v=" + state["youtube_id"])
        print("FACEBOOK https://www.facebook.com/watch/?v=" + state["facebook_id"])
        print("PUBLICACION FINALIZADA", flush=True)


if __name__ == "__main__":
    main()
