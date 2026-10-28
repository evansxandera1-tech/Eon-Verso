#!/usr/bin/env python3
"""Publicador independiente para Todo Mal. Usa exclusivamente su OAuth."""
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
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

FOLDER = "Todo Mal/Publicar/Vecino4A"
DEFAULT_VIDEO = "vecino4a_corregido.mp4"
DEFAULT_THUMB = "miniatura.jpg"
SUB_FILES = {"es": "es.srt", "en": "en.srt"}
LOCK_FILE = "todo-mal-canal.json"
STATE_FILE = "todo-mal-publicaciones.json"
API = "https://api.github.com/repos/{}/contents/{}"
DEFAULT_DESCRIPTION = """Te mudaste al departamento 4B. Tu vecino del 4A parece tranquilo, hasta que encuentras una nota con 11 reglas que jamás debes romper.

Las 11 reglas del vecino del 4A es una historia de terror animada sobre un edificio extraño, un vecino que sabe demasiado y una última regla que no deberías ignorar.

Narración en español. Subtítulos disponibles en español e inglés.

#HistoriasDeTerror #TerrorAnimado #Creepypasta"""


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
        "message": "Todo Mal: guardar identificación/publicación",
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
        refresh_token=need("TODOMAL_YOUTUBE_REFRESH_TOKEN"),
        client_id=need("TODOMAL_YOUTUBE_CLIENT_ID"),
        client_secret=need("TODOMAL_YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False), creds


def validate_channel(yt, mode):
    channels = yt.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if len(channels) != 1:
        raise RuntimeError("OAuth no identificó un único canal. Reautoriza Todo Mal.")
    item = channels[0]
    title = item.get("snippet", {}).get("title", "")
    cid = item["id"]
    handle = item.get("snippet", {}).get("customUrl", "")
    normalized = re.sub(r"[^a-z0-9]", "", (title + " " + handle).casefold())
    if "todomal" not in normalized:
        raise RuntimeError("CANAL INCORRECTO: autorizado '" + title + "' (" + cid +
                           "). Debes seleccionar Todo Mal, no EON-Verso.")
    saved, sha = gh_request(LOCK_FILE)
    if saved and saved.get("channel_id") != cid:
        raise RuntimeError("CANAL DIFERENTE AL PRIMER CANAL VERIFICADO. Publicación bloqueada.")
    if mode == "identificar" and not saved:
        save_state(LOCK_FILE, {"channel_id": cid, "nombre_inicial": title}, sha)
        print("CANAL FIJADO de forma segura: " + title + " / " + cid, flush=True)
    elif mode != "identificar" and not saved:
        save_state(LOCK_FILE, {"channel_id": cid, "nombre_inicial": title}, sha)
        print("CANAL TODO MAL FIJADO: " + title + " / " + cid, flush=True)
    print("CANAL VERIFICADO: " + title + " (" + cid + ")", flush=True)
    return cid


def local_copy(folder, filename, minimum=1_048_576):
    if filename != Path(filename).name or not filename or filename.startswith("."):
        raise ValueError("Nombre de archivo inválido; usa solo el nombre del archivo.")
    target = folder / filename
    source = "gdrive:" + FOLDER + "/" + filename
    subprocess.run(["rclone", "copyto", source, str(target)], check=True, timeout=1800)
    if not target.is_file() or target.stat().st_size < minimum:
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
            "tags": ["historias de terror", "terror animado", "historias de miedo",
                     "reglas de terror", "creepypasta español", "vecino 4A",
                     "habitación 4A", "11 reglas", "relatos de terror", "Todo Mal"],
            "categoryId": "24",
            "defaultLanguage": "es",
        },
        "status": {
            "privacyStatus": "private",
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



def require_captions_permission(creds):
    """Check permission before uploading a video that requires bilingual captions."""
    if not creds.valid:
        creds.refresh(Request())
    response = requests.get("https://oauth2.googleapis.com/tokeninfo",
                            params={"access_token": creds.token}, timeout=30)
    response.raise_for_status()
    granted = set(response.json().get("scope", "").split())
    required = "https://www.googleapis.com/auth/youtube.force-ssl"
    if required not in granted:
        raise RuntimeError(
            "FALTA PERMISO PARA SUBTITULOS: el Refresh Token no incluye youtube.force-ssl. "
            "Genera otro Refresh Token autorizando DOS permisos: "
            "https://www.googleapis.com/auth/youtube y "
            "https://www.googleapis.com/auth/youtube.force-ssl. "
            "Sustituye solo TODOMAL_YOUTUBE_REFRESH_TOKEN. "
            "No se ha subido el video."
        )
    print("PERMISOS OK: youtube.force-ssl habilita subtitulos", flush=True)


def validate_subtitles(es, en):
    import re
    def marks(path):
        data = path.read_text(encoding="utf-8-sig")
        pairs = re.findall(r"(?m)^(\d\d:\d\d:\d\d,\d{3}) --> "
                           r"(\d\d:\d\d:\d\d,\d{3})", data)
        if len(pairs) < 20:
            raise RuntimeError("SRT incompleto: " + path.name)
        return pairs
    es_marks, en_marks = marks(es), marks(en)
    if es_marks != en_marks:
        raise RuntimeError("Los SRT español e inglés no están sincronizados.")
    print("SRT OK: %s intervalos idénticos para español e inglés" %
          len(es_marks), flush=True)


def upload_caption(yt, video_id, lang, path):
    name = "Español" if lang == "es" else "English"
    existing = yt.captions().list(part="snippet", videoId=video_id).execute()
    for item in existing.get("items", []):
        sn = item.get("snippet", {})
        if sn.get("language") == lang and sn.get("name") == name:
            print("SUBTITULOS YA EXISTEN: " + name, flush=True)
            return
    yt.captions().insert(
        part="snippet",
        body={"snippet": {"videoId": video_id, "language": lang,
                          "name": name, "isDraft": False}},
        media_body=MediaFileUpload(str(path), mimetype="application/octet-stream"),
    ).execute()
    print("SUBTITULOS SUBIDOS: " + name, flush=True)


def upload_thumbnail(yt, video_id, path):
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    yt.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(path), mimetype=mime)
    ).execute()
    print("MINIATURA PERSONALIZADA SUBIDA", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--modo", choices=("identificar", "verificar", "publicar"), required=True)
    parser.add_argument("--video", default=DEFAULT_VIDEO)
    parser.add_argument("--titulo", default="Las 11 reglas del vecino del 4A | Historia de terror animada")
    parser.add_argument("--privacidad", choices=("private", "unlisted", "public"), default="public")
    args = parser.parse_args()

    if args.video.lower()[-4:] != ".mp4":
        raise ValueError("Solo archivos MP4.")
    if not 1 <= len(args.titulo) <= 100 or "\n" in args.titulo:
        raise ValueError("El título debe tener de 1 a 100 caracteres y una sola línea.")

    yt, creds = youtube()
    channel_id = validate_channel(yt, args.modo)
    if args.modo == "identificar":
        print("LISTO: canal autorizado y bloqueado. Aún no se ha subido ningún video.")
        return

    with tempfile.TemporaryDirectory(prefix="todo_mal_") as tmp:
        folder = Path(tmp)
        video = local_copy(folder, args.video)
        subs = {lang: local_copy(folder, name, minimum=100)
                for lang, name in SUB_FILES.items()}
        thumb = local_copy(folder, DEFAULT_THUMB, minimum=1000)
        validate_subtitles(subs["es"], subs["en"])
        file_hash = digest(video)
        print("DRIVE OK: " + args.video + " (" + str(video.stat().st_size) +
              " bytes), SHA256=" + file_hash, flush=True)

        state, state_sha = gh_request(STATE_FILE)
        key = channel_id + ":" + file_hash
        if args.modo == "verificar":
            require_captions_permission(creds)
            print("VERIFICACION OK. Video, dos SRT, miniatura y permisos listos. "
                  "No se publico nada.", flush=True)
            return

        # Do not upload if subtitles cannot subsequently be attached.
        require_captions_permission(creds)

        info = state.get(key)
        if info:
            video_id = info["youtube_id"]
            print("CONTINUANDO VIDEO EXISTENTE: https://www.youtube.com/watch?v=" +
                  video_id, flush=True)
        else:
            existing_id = find_existing(yt, args.titulo)
            if existing_id:
                raise RuntimeError("Ya existe un video con ese titulo en este canal: "
                                   "https://www.youtube.com/watch?v=" + existing_id)
            video_id = upload(yt, video, args.titulo, args.privacidad)
            info = {
                "youtube_id": video_id, "channel_id": channel_id,
                "archivo": args.video, "sha256": file_hash,
                "titulo": args.titulo, "privacidad_solicitada": args.privacidad,
                "miniatura": False, "subtitulos_es": False, "subtitulos_en": False
            }
            state[key] = info
            save_state(STATE_FILE, state, state_sha)
            print("VIDEO SUBIDO EN PRIVADO: https://www.youtube.com/watch?v=" +
                  video_id, flush=True)

        # Each operation is idempotent. If one fails, re-running completes missing assets.
        if not info.get("miniatura"):
            upload_thumbnail(yt, video_id, thumb)
            info["miniatura"] = True
        if not info.get("subtitulos_es"):
            upload_caption(yt, video_id, "es", subs["es"])
            info["subtitulos_es"] = True
        if not info.get("subtitulos_en"):
            upload_caption(yt, video_id, "en", subs["en"])
            info["subtitulos_en"] = True
        # Refresh SHA because initial upload may already have written STATE_FILE.
        _, latest_sha = gh_request(STATE_FILE)
        save_state(STATE_FILE, state, latest_sha)
        if args.privacidad != "private":
            yt.videos().update(
                part="status",
                body={"id": video_id, "status": {"privacyStatus": args.privacidad,
                      "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
            ).execute()
            print("VISIBILIDAD FINAL: " + args.privacidad, flush=True)
        print("COMPLETADO: video, miniatura, subtitulos ES y EN. "
              "https://www.youtube.com/watch?v=" + video_id, flush=True)
        print("Revisa la visibilidad en YouTube Studio.", flush=True)


if __name__ == "__main__":
    main()
