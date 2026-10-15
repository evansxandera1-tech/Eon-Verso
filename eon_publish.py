#!/usr/bin/env python3
"""Publicador EON-Verso. Verifica la identidad ANTES de subir contenido."""
import argparse
import base64
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

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "eon_shorts.json").read_text(encoding="utf-8"))
EXPECTED_YT = "UCQI8q5L9aHk8BvznYj9hxsg"
GRAPH_VERSION = "v25.0"
HEADINGS = (
    "TÍTULO PARA YOUTUBE SHORTS", "DESCRIPCIÓN", "HASHTAGS",
    "ETIQUETAS (YOUTUBE STUDIO)", "NOTA DE PUBLICACIÓN",
)

def require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("Falta configurar el secreto " + name)
    return value

def rclone_file(file_id, destination):
    proc = subprocess.run(
        ["rclone", "backend", "copyid", "gdrive:", file_id, str(destination)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
    )
    if proc.returncode:
        raise RuntimeError("No se pudo leer un archivo de Drive por ID. Revisa RCLONE_CONFIG_BASE64 y el acceso a EON-Verso.")
    if not destination.exists() or not destination.stat().st_size:
        raise RuntimeError("El archivo descargado de Drive está vacío.")

def parse_metadata(text):
    sections = {}
    current = None
    for line in text.replace("\r", "").split("\n"):
        key = line.strip().upper()
        if key in HEADINGS:
            current = key
            sections[current] = []
        elif current:
            sections[current].append(line)
    title = "\n".join(sections.get(HEADINGS[0], [])).strip()
    desc = "\n".join(sections.get("DESCRIPCIÓN", [])).strip()
    hashtags = " ".join(sections.get("HASHTAGS", [])).strip()
    tag_txt = " ".join(sections.get("ETIQUETAS (YOUTUBE STUDIO)", [])).strip()
    if not title or not desc:
        raise RuntimeError("YouTube.txt debe incluir TÍTULO y DESCRIPCIÓN.")
    if len(title) > 100:
        raise RuntimeError("El título supera 100 caracteres: " + title)
    tags = [x.strip() for x in tag_txt.split(",") if x.strip()][:20]
    yt_desc = desc + ("\n\n" + hashtags if hashtags else "")
    # Para Facebook omitir instrucciones internas y textos exclusivos de YouTube Studio.
    fb_lines = [line for line in desc.splitlines() if not any(
        phrase in line.lower() for phrase in (
            "youtube studio", "vídeo relacionado", "video relacionado",
            "suscríbete a eon-verso", "suscríbete!", "canal: @eonverso",
        )
    )]
    fb_desc = "\n".join(fb_lines).strip() + ("\n\n" + hashtags if hashtags else "")
    return {"title": title, "youtube_description": yt_desc[:5000],
            "facebook_description": fb_desc[:5000], "tags": tags}

def youtube_client():
    credentials = Credentials(
        token=None, refresh_token=require("YOUTUBE_REFRESH_TOKEN"),
        client_id=require("YOUTUBE_CLIENT_ID"),
        client_secret=require("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)

def verify_youtube():
    youtube = youtube_client()
    try:
        result = youtube.channels().list(part="id,snippet", mine=True).execute()
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
        raw = getattr(exc, "content", b"")
        try:
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            api_error = payload.get("error", {})
            reason = api_error.get("errors", [{}])[0].get("reason", "desconocido")
            message = api_error.get("message", "Sin detalles")
        except (ValueError, TypeError, IndexError, AttributeError):
            reason, message = "desconocido", "Sin detalles"
        # Los errores de renovación OAuth (RefreshError) no son respuestas HTTP de YouTube.
        # Su texto permite distinguir invalid_grant de invalid_client y de permisos insuficientes.
        detail = str(exc)
        detail = re.sub(r"(?i)(access_token|refresh_token|client_secret|authorization)\\?[\"\']?\\?\s*[:=]\\?\s*[\"\']?[^,}\s]+", r"\\1=[OCULTO]", detail)
        raise RuntimeError("Falló autenticación/verificación de YouTube (" + type(exc).__name__ +
            ", HTTP " + str(status or "?") + ", razón: " + str(reason) +
            ", mensaje: " + str(message)[:180] + ", detalle: " + detail[:350] + "). No se publicó nada.") from None
    channels = result.get("items", [])
    if len(channels) != 1 or channels[0].get("id") != EXPECTED_YT:
        names = ", ".join(c.get("snippet", {}).get("title", "desconocido") for c in channels) or "ninguno"
        raise RuntimeError("OAuth de YouTube NO apunta a EON-Verso. Canal autorizado: " + names + ". Detenido por seguridad.")
    print("YOUTUBE OK: " + channels[0].get("snippet", {}).get("title", "EON-Verso"), flush=True)
    return youtube

def verify_facebook():
    page_id = require("FACEBOOK_PAGE_ID")
    token = require("FACEBOOK_PAGE_TOKEN")
    r = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}",
        params={"fields": "id,name", "access_token": token}, timeout=35)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError("Facebook no devolvió datos válidos al comprobar la página.") from None
    if not r.ok:
        code = data.get("error", {}).get("code", "desconocido")
        raise RuntimeError("Facebook no permitió comprobar la página (código " + str(code) + "). Revisa PAGE_TOKEN.") from None
    page_name = data.get("name", "")
    normalized = re.sub(r"[^a-z0-9]+", "", page_name.casefold())
    if data.get("id") != page_id or "eonverso" not in normalized:
        raise RuntimeError("El token Facebook está ligado a '" + page_name + "', no a la página EON-Verso. Detenido.")
    print("FACEBOOK OK: " + page_name, flush=True)
    return page_id, token

def gh_state(action, new_state=None, previous_sha=None):
    token = require("GH_TOKEN")
    repo = require("GITHUB_REPOSITORY")
    url = f"https://api.github.com/repos/{repo}/contents/eon-publication-state.json"
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if action == "read":
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 404:
            return {}, None
        r.raise_for_status()
        data = r.json()
        return json.loads(base64.b64decode(data["content"])), data["sha"]
    body = {
        "message": "Registrar publicación EON-Verso sin duplicados",
        "content": base64.b64encode(
            (json.dumps(new_state, ensure_ascii=False, indent=2) + "\n").encode()
        ).decode(),
    }
    if previous_sha:
        body["sha"] = previous_sha
    r = requests.put(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["content"]["sha"]

def youtube_upload(client, video, metadata):
    result = client.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": metadata["title"],
                "description": metadata["youtube_description"],
                "tags": metadata["tags"],
                "categoryId": "27",
                "defaultLanguage": "es",
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        },
        media_body=MediaFileUpload(str(video), mimetype="video/mp4",
                                   chunksize=8 * 1024 * 1024, resumable=True),
    ).execute()
    return result["id"]

def facebook_upload(page_id, token, video, metadata):
    # El endpoint /videos de Página admite vídeos de más de 60 segundos.
    # No presupone que Facebook los clasificará como Reels automáticamente.
    with video.open("rb") as handle:
        response = requests.post(
            f"https://graph-video.facebook.com/{GRAPH_VERSION}/{page_id}/videos",
            data={"access_token": token, "title": metadata["title"],
                  "description": metadata["facebook_description"], "published": "true"},
            files={"source": (video.name, handle, "video/mp4")},
            timeout=600,
        )
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Facebook devolvió respuesta de subida no válida.") from None
    if not response.ok or not data.get("id"):
        code = data.get("error", {}).get("code", "desconocido")
        raise RuntimeError("Facebook rechazó la subida (código " + str(code) + ").")
    return data["id"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["verify", "publish"], default="verify")
    parser.add_argument("--short", default="perros-01")
    parser.add_argument("--platforms", choices=["both", "youtube", "facebook"], default="both")
    parser.add_argument("--skip-drive-check", action="store_true", help="Diagnóstico de OAuth YouTube sin consultar Drive")
    args = parser.parse_args()
    if args.short != "todos" and args.short not in MANIFEST:
        raise RuntimeError("Short no existe: " + args.short)
    selected = sorted(MANIFEST) if args.short == "todos" else [args.short]
    platforms = ["youtube", "facebook"] if args.platforms == "both" else [args.platforms]

    # Diagnóstico separado de las tres conexiones; no subir si falla alguna seleccionada.
    failures = []
    yt = None
    fb = None
    if "youtube" in platforms:
        try:
            yt = verify_youtube()
        except Exception as exc:
            failures.append("YouTube: " + str(exc))
    if "facebook" in platforms:
        try:
            fb = verify_facebook()
        except Exception as exc:
            failures.append("Facebook: " + str(exc))
    with tempfile.TemporaryDirectory(prefix="eon_") as temp:
        target = Path(temp) / "YouTube.txt"
        try:
            if args.skip_drive_check and args.mode == "verify":
                print("DRIVE OMITIDO: prueba exclusiva de OAuth.", flush=True)
            else:
                rclone_file(MANIFEST[selected[0]]["metadata_id"], target)
                sample = parse_metadata(target.read_text(encoding="utf-8-sig"))
                print("DRIVE OK. Ejemplo de título: " + sample["title"], flush=True)
        except Exception as exc:
            failures.append("Google Drive: " + str(exc))
        if failures:
            for message in failures:
                print("VERIFICACIÓN FALLIDA: " + message, flush=True)
            raise RuntimeError("Faltan " + str(len(failures)) + " verificaciones; no se ha publicado ningún video.")
        if args.mode == "verify":
            print("VERIFICACIÓN COMPLETA: no se ha subido ningún video.", flush=True)
            return

        state, sha = gh_state("read")
        for short_id in selected:
            entry = MANIFEST[short_id]
            pending = [p for p in platforms if p not in state.get(short_id, {})]
            if not pending:
                print(short_id + ": todas las plataformas ya están registradas. Omitido.")
                continue
            print(short_id + ": preparando " + ", ".join(pending), flush=True)
            with tempfile.TemporaryDirectory(prefix="clip_") as clip_temp:
                clip_dir = Path(clip_temp)
                video = clip_dir / entry["filename"]
                text_file = clip_dir / "YouTube.txt"
                rclone_file(entry["metadata_id"], text_file)
                metadata = parse_metadata(text_file.read_text(encoding="utf-8-sig"))
                rclone_file(entry["video_id"], video)
                for platform in pending:
                    # Nunca volver a subir un video cuyo resultado ya esté registrado.
                    if platform == "youtube":
                        published_id = youtube_upload(yt, video, metadata)
                        public_link = "https://www.youtube.com/watch?v=" + published_id
                    else:
                        published_id = facebook_upload(fb[0], fb[1], video, metadata)
                        public_link = "https://www.facebook.com/" + published_id
                    # Registrar enseguida la subida exitosa, antes de la siguiente.
                    state.setdefault(short_id, {})[platform] = {
                        "id": published_id, "url": public_link, "drive_file_id": entry["video_id"]
                    }
                    try:
                        sha = gh_state("write", state, sha)
                    except Exception:
                        raise RuntimeError("Se publicó " + short_id + " en " + platform +
                            " (ID " + published_id + "), PERO falló el registro antidupli" +
                            "cados. No repitas esta ejecución sin comprobarlo.") from None
                    print("PUBLICADO " + short_id + " en " + platform + ": " + public_link, flush=True)

if __name__ == "__main__":
    main()
