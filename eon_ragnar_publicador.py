#!/usr/bin/env python3
"""Publicador dedicado V2 1080p recibida -> YouTube EON-Verso + Facebook Ojo al Piojo.

Reconstruye las 16 partes en Drive y verifica SHA256 antes de publicar.
Usa los secretos ya existentes en el repositorio HSF/Eon-Verso.
"""
import argparse
import hashlib
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
VIDEO_DRIVE_PATH = "EON-Verso/Publicacion_Ragnar/V2_ARCHIVO_PARTES"
EXPECTED_SHA256 = "2342ab59482a9d15fa070bb3313cfe863978ea13e32da79df60809fb17109563"
EXPECTED_SIZE = 266095087
PART_COUNT = 16
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


def rebuild_original_v2(directory, dest):
    """Descarga los fragmentos de Drive en orden y reconstruye el MP4 exacto de usuario."""
    total = 0
    dig = hashlib.sha256()
    with dest.open("wb") as target:
        for part_no in range(1, PART_COUNT + 1):
            part_name = f"ragnar_v2_1080p.part{part_no:02d}"
            part = directory / part_name
            rclone_copy(["copyto", "gdrive:" + VIDEO_DRIVE_PATH + "/" + part_name], part)
            expected_part = 16 * 1024 * 1024 if part_no < PART_COUNT else EXPECTED_SIZE - 15 * 16 * 1024 * 1024
            if part.stat().st_size != expected_part:
                raise RuntimeError(f"Parte {part_no} invalida: {part.stat().st_size} bytes; se esperaban {expected_part}")
            with part.open("rb") as stream:
                while True:
                    chunk = stream.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    dig.update(chunk)
                    total += len(chunk)
            part.unlink()
            print(f"DRIVE RAGNAR: parte {part_no}/{PART_COUNT} OK", flush=True)
    if total != EXPECTED_SIZE or dig.hexdigest() != EXPECTED_SHA256:
        raise RuntimeError(f"SHA256 del MP4 recibido no coincide: bytes={total}, hash={dig.hexdigest()}")
    print("SHA256 ORIGINAL RECIBIDO VERIFICADO:", dig.hexdigest(), flush=True)


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
        raise RuntimeError(f"V2 incorrecta: duracion {dur:.2f}, audio/video faltante.")
    if video.get("width", 0) < 1280 or video.get("height", 0) < 720:
        raise RuntimeError("Video V2 inferior a 720p.")
    print(f"VIDEO V2 OK: {dur:.2f}s, {video['width']}x{video['height']}", flush=True)


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


def existing_facebook_video(token, title):
    """Comprueba si Facebook ya aceptó la publicación anterior."""
    res = requests.get(
        f"https://graph.facebook.com/{GRAPH}/{FB_ID}/videos",
        params={"access_token": token, "fields": "id,title,created_time",
                "limit": 100}, timeout=40)
    try:
        payload = res.json()
    except ValueError:
        raise RuntimeError("Facebook no devolvió JSON al comprobar videos existentes (HTTP %s)" % res.status_code)
    if not res.ok or "data" not in payload:
        raise RuntimeError("No se pudo consultar la lista de videos Facebook: HTTP %s: %s"
                           % (res.status_code, str(payload.get("error", {}))[:350]))
    expected = title.strip().casefold()
    for item in payload["data"]:
        if item.get("title", "").strip().casefold() == expected:
            print("FACEBOOK EXISTENTE; SE EVITA DUPLICADO:", item.get("id"), flush=True)
            return item["id"]
    print("FACEBOOK: no hay publicación anterior con ese título", flush=True)
    return None


def fb_json(res, operation):
    try:
        value = res.json()
    except ValueError:
        raise RuntimeError("Facebook %s respondió HTTP %s con contenido no JSON (%s, %d bytes)"
                           % (operation, res.status_code, res.headers.get("content-type", "-"),
                              len(res.content)))
    if not res.ok or value.get("error"):
        raise RuntimeError("Facebook %s rechazado: HTTP %s: %s"
                           % (operation, res.status_code, str(value.get("error", value))[:380]))
    return value


def upload_facebook(token, movie, title, description):
    """Usar protocolo reanudable para archivos de 254 MB."""
    endpoint = f"https://graph-video.facebook.com/{GRAPH}/{FB_ID}/videos"
    started = fb_json(requests.post(endpoint, data={
        "access_token": token, "upload_phase": "start",
        "file_size": str(movie.stat().st_size)}, timeout=90), "start")
    session = started["upload_session_id"]
    offset = int(started["start_offset"])
    end = int(started["end_offset"])
    file_size = movie.stat().st_size
    print("FACEBOOK: sesión resumible iniciada", flush=True)
    with movie.open("rb") as stream:
        while offset < file_size:
            amount = min(end - offset, file_size - offset)
            if amount <= 0:
                raise RuntimeError("Facebook devolvió rango de transferencia vacío.")
            stream.seek(offset)
            chunk = stream.read(amount)
            res = requests.post(endpoint,
                data={"access_token": token, "upload_phase": "transfer",
                      "upload_session_id": session, "start_offset": str(offset)},
                files={"video_file_chunk": ("fragmento.mp4", chunk, "application/octet-stream")},
                timeout=300)
            answer = fb_json(res, "transfer")
            new_offset = int(answer["start_offset"])
            if new_offset <= offset:
                raise RuntimeError("Facebook no avanzó el offset de video.")
            offset = new_offset
            end = int(answer["end_offset"])
            print("FACEBOOK: %.1f%%" % (100*min(offset,file_size)/file_size), flush=True)
    finished = fb_json(requests.post(endpoint, data={
        "access_token": token, "upload_phase": "finish",
        "upload_session_id": session, "title": title,
        "description": description, "published": "true"}, timeout=120), "finish")
    video_id = started.get("video_id") or finished.get("id") or finished.get("video_id")
    if not video_id:
        video_id = existing_facebook_video(token, title)
    if not video_id:
        raise RuntimeError("Facebook finalizó carga pero no devolvió ID de video.")
    return str(video_id)


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
        if "facebook_id" not in state:
            already = existing_facebook_video(fb_token, fb_title)
            if already:
                state["facebook_id"] = already
                sha = save_state(state, sha)
        if state.get("youtube_id") and state.get("thumbnail_done") and state.get("facebook_id"):
            print("TODO YA PUBLICADO, NO DUPLICAR", flush=True)
            print("YOUTUBE https://www.youtube.com/watch?v=" + state["youtube_id"])
            print("FACEBOOK https://www.facebook.com/watch/?v=" + state["facebook_id"])
            return
        movie = work / "EON_VERSO_RAGNAR_MONTAJE_CINEMATOGRAFICO_V2_1080P.mp4"
        print("Reconstruyendo V2 1080p recibida (16 partes de Drive)...", flush=True)
        rebuild_original_v2(work, movie)
        verify_movie(movie)

        if opts.mode == "verify":
            print("VERIFICACION OK. NO SE PUBLICO.", flush=True)
            return

        if "youtube_id" not in state:
            state["youtube_id"] = upload_youtube(yt, movie, title, description, tags)
            state["source"] = VIDEO_DRIVE_PATH + "/ (SHA256 " + EXPECTED_SHA256 + ")"
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
