#!/usr/bin/env python3
"""Publicador independiente para Laboratorio Mental. No usa tokens de EON-Verso."""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

FOLDER = "Laboratorio Mental/Publicar"
LOCK_FILE = "laboratorio-mental-canal.json"
STATE_FILE = "laboratorio-mental-publicaciones.json"
API = "https://api.github.com/repos/{}/contents/{}"
DEFAULT_DESCRIPTION = (
    "A nadie le importan tus excusas. Lo que importa es lo que haces a partir de hoy.\n\n"
    "En Laboratorio Mental hablamos de disciplina, hábitos y motivación para avanzar "
    "incluso cuando las circunstancias son difíciles.\n\n"
    "Suscríbete para recibir más reflexiones sobre disciplina y crecimiento personal.\n\n"
    "Narración y producción originales con asistencia de inteligencia artificial.\n"
    "#Disciplina #Motivacion #LaboratorioMental"
)


def need(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("Falta configurar " + name)
    return value


def gh_request(path, method="GET", data=None):
    url = API.format(need("GITHUB_REPOSITORY"), path)
    headers = {
        "Authorization": "Bearer " + need("GH_TOKEN"),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.request(method, url, headers=headers, json=data, timeout=35)
    if response.status_code == 404 and method == "GET":
        return {}, None
    response.raise_for_status()
    if method == "GET":
        obj = response.json()
        return json.loads(base64.b64decode(obj["content"])), obj["sha"]
    return response.json(), None


def save_state(path, data, sha=None):
    payload = {
        "message": "Laboratorio Mental: guardar identificación/publicación",
        "content": base64.b64encode(
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    gh_request(path, "PUT", payload)


def youtube():
    creds = Credentials(
        token=None,
        refresh_token=need("LAB_YOUTUBE_REFRESH_TOKEN"),
        client_id=need("LAB_YOUTUBE_CLIENT_ID"),
        client_secret=need("LAB_YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def validate_channel(yt, mode):
    channels = yt.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if len(channels) != 1:
        raise RuntimeError("OAuth no identificó un único canal. Reautoriza Laboratorio Mental.")
    item = channels[0]
    title = item.get("snippet", {}).get("title", "")
    cid = item["id"]
    normalized = re.sub(r"[^a-z0-9]", "", title.casefold())
    if "laboratoriomental" not in normalized:
        raise RuntimeError("CANAL INCORRECTO: autorizado '" + title + "' (" + cid +
                           "). Debes seleccionar Laboratorio Mental, no EON-Verso.")
    saved, sha = gh_request(LOCK_FILE)
    if saved and saved.get("channel_id") != cid:
        raise RuntimeError("CANAL DIFERENTE AL PRIMER CANAL VERIFICADO. Publicación bloqueada.")
    if mode == "identificar" and not saved:
        save_state(LOCK_FILE, {"channel_id": cid, "nombre_inicial": title}, sha)
        print("CANAL FIJADO de forma segura: " + title + " / " + cid, flush=True)
    elif mode != "identificar" and not saved:
        raise RuntimeError("Primero ejecuta 'identificar' para fijar el canal correcto.")
    print("CANAL VERIFICADO: " + title + " (" + cid + ")", flush=True)
    return cid


def local_copy(folder, filename):
    if filename != Path(filename).name or not filename or filename.startswith("."):
        raise ValueError("Nombre de archivo inválido; usa solo el nombre del MP4.")
    target = folder / filename
    source = "gdrive:" + FOLDER + "/" + filename
    subprocess.run(["rclone", "copyto", source, str(target)], check=True, timeout=1800)
    if not target.is_file() or target.stat().st_size < 1024 * 1024:
        raise RuntimeError("Archivo ausente, vacío o demasiado pequeño en Drive: " + filename)
    return target


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_existing(yt, title):
    result = yt.search().list(part="snippet", forMine=True, type="video",
                              q=title, maxResults=50).execute()
    for item in result.get("items", []):
        if item["snippet"]["title"].strip().casefold() == title.casefold():
            return item["id"]["videoId"]
    return None


def upload(yt, path, title, privacy):
    body = {
        "snippet": {
            "title": title,
            "description": DEFAULT_DESCRIPTION,
            "tags": ["disciplina", "motivación", "hábitos", "superación personal",
                     "desarrollo personal", "Laboratorio Mental"],
            "categoryId": "27",
            "defaultLanguage": "es",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    request = yt.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(path), mimetype="video/mp4",
                                   chunksize=8 * 1024 * 1024, resumable=True),
    )
    result = None
    while result is None:
        progress, result = request.next_chunk()
        if progress:
            print("Subido: %.0f%%" % (100 * progress.progress()), flush=True)
    return result["id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--modo", choices=("identificar", "verificar", "publicar"), required=True)
    parser.add_argument("--video", default="EON_Excusas_Subtitulado.mp4")
    parser.add_argument("--titulo", default="A Nadie Le Importan Tus Excusas | La Disciplina Cambia Tu Vida")
    parser.add_argument("--privacidad", choices=("private", "unlisted", "public"), default="private")
    args = parser.parse_args()

    if args.video.lower()[-4:] != ".mp4":
        raise ValueError("Solo archivos MP4.")
    if not 1 <= len(args.titulo) <= 100 or "\n" in args.titulo:
        raise ValueError("El título debe tener de 1 a 100 caracteres y una sola línea.")

    yt = youtube()
    channel_id = validate_channel(yt, args.modo)
    if args.modo == "identificar":
        print("LISTO: canal autorizado y bloqueado. Aún no se ha subido ningún video.")
        return

    with tempfile.TemporaryDirectory(prefix="laboratorio_mental_") as tmp:
        video = local_copy(Path(tmp), args.video)
        file_hash = digest(video)
        print("DRIVE OK: " + args.video + " (" + str(video.stat().st_size) +
              " bytes), SHA256=" + file_hash, flush=True)

        state, state_sha = gh_request(STATE_FILE)
        key = channel_id + ":" + file_hash
        if key in state:
            print("YA PUBLICADO: https://www.youtube.com/watch?v=" + state[key]["youtube_id"])
            return
        if args.modo == "verificar":
            print("VERIFICACIÓN OK. No se publicó. Título: " + args.titulo)
            return

        existing_id = find_existing(yt, args.titulo)
        if existing_id:
            raise RuntimeError(
                "Ya hay un video con ese título en Laboratorio Mental: "
                "https://www.youtube.com/watch?v=" + existing_id +
                ". Se detuvo para evitar duplicados.")

        video_id = upload(yt, video, args.titulo, args.privacidad)
        state[key] = {
            "youtube_id": video_id,
            "channel_id": channel_id,
            "archivo": args.video,
            "sha256": file_hash,
            "titulo": args.titulo,
            "privacidad_solicitada": args.privacidad,
        }
        save_state(STATE_FILE, state, state_sha)
        print("VIDEO SUBIDO: https://www.youtube.com/watch?v=" + video_id, flush=True)
        print("Comprueba en YouTube Studio su estado real de privacidad.", flush=True)


if __name__ == "__main__":
    main()
