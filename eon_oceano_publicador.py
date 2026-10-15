#!/usr/bin/env python3
"""Publica exclusivamente el documental del océano en YouTube EON-Verso y Facebook Ojo al Piojo.

Se verifica canal y página antes de publicar. No afecta los Shorts. Guarda un registro
de resultados en GitHub y evita publicar de nuevo en plataformas ya registradas.
"""
import argparse, base64, json, os, subprocess, tempfile
from pathlib import Path
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

YT_CHANNEL_ID = "UCQI8q5L9aHk8BvznYj9hxsg"
FB_PAGE_ID = "111079017290300"
FB_PAGE_NAME = "Ojo al Piojo"
VIDEO_DRIVE_ID = "1RmMaxaacbLSTlvywghHZS_8Dgl2QTsm7"
THUMB_DRIVE_ID = "1bN2ZV-bUQBC_T44h-2XvGQ9X-zMi9tEY"
STATE_PATH = "eon-oceano-publication-state.json"
GRAPH_VERSION = "v25.0"
YT_TITLE = "¿Por qué sabemos más de la Luna que del fondo del océano?"
YT_TAGS = [
 "fondo del océano", "fondo del mar", "océano profundo", "misterios del océano",
 "exploración oceánica", "profundidades del mar", "fosa de las marianas",
 "abismo challenger", "sonar", "cartografía oceánica", "vida submarina",
 "fuentes hidrotermales", "ciencia", "curiosidades científicas",
 "documentales en español", "EON-Verso",
]
YT_DESCRIPTION = """¿Cómo es posible que conozcamos mejor la superficie de la Luna que gran parte del fondo de nuestros propios océanos? 🌊🌕

El océano cubre la mayor parte de nuestro planeta, pero explorar sus profundidades sigue siendo uno de los desafíos científicos más difíciles.

En este documental de EON-Verso descubrirás por qué los satélites no pueden mostrarnos todo lo que hay bajo el agua, cómo funciona el sonar, qué ocurre cuando descendemos a miles de metros y por qué existen ecosistemas capaces de sobrevivir sin luz solar.

También conocerás los misterios de las fosas oceánicas, las fuentes hidrotermales y el dilema que plantea explorar y explotar un mundo que apenas estamos comenzando a conocer.

La pregunta es sencilla: ¿por qué hemos conseguido cartografiar la Luna con tanto detalle mientras grandes regiones del fondo oceánico siguen sin estudiarse a fondo?

🔔 Suscríbete a EON-Verso para descubrir nuevas historias sobre ciencia, naturaleza, historia y los misterios de nuestro planeta.

💬 ¿Qué te gustaría que los científicos descubrieran en el fondo del océano?

Aviso: Este documental emplea imágenes ilustrativas y narración creadas con inteligencia artificial. No son grabaciones reales de expediciones submarinas.

#OceanoProfundo #FondoDelMar #Curiosidades"""
FB_TITLE = "¿Por qué sabemos más de la Luna que del fondo del océano? 🌊🌕"
FB_DESCRIPTION = """🌊🌕 ¡Un misterio está justo debajo de nuestros pies!

¿Sabías que tenemos mapas más detallados de la Luna que de grandes zonas del fondo de nuestros océanos?

¿Por qué resulta tan difícil explorar el fondo del mar? ¿Qué sucede con la luz, la presión y las comunicaciones a miles de metros de profundidad?

En este documental descubrirás cómo funciona el sonar, qué se esconde en las grandes fosas y cómo puede existir vida sin luz solar.

▶️ Mira el documental completo y cuéntanos: ¿qué descubrirías si pudieras descender al punto más profundo del océano?

👍 Sigue Ojo al Piojo para más curiosidades de ciencia y naturaleza.

Contenido ilustrativo elaborado con herramientas de inteligencia artificial; las escenas no representan grabaciones reales de expediciones.

#MisteriosDelOceano #Curiosidades #Ciencia #FondoDelMar"""

def require(name):
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError("Falta variable/secreto: " + name)
    return val

def google_client():
    credentials = Credentials(
        token=None, refresh_token=require("YOUTUBE_REFRESH_TOKEN"),
        client_id=require("YOUTUBE_CLIENT_ID"),
        client_secret=require("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)

def verify_youtube():
    client = google_client()
    channels = client.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if len(channels) != 1 or channels[0].get("id") != YT_CHANNEL_ID:
        raise RuntimeError("Canal YouTube no coincide con EON-Verso. No se publica.")
    print("CANAL YOUTUBE VERIFICADO: EON-Verso", flush=True)
    return client

def verify_facebook():
    pid = require("FACEBOOK_PAGE_ID")
    if pid != FB_PAGE_ID:
        raise RuntimeError("FACEBOOK_PAGE_ID no coincide con Ojo al Piojo. No se publica.")
    token = require("FACEBOOK_PAGE_TOKEN")
    response = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{pid}",
        params={"fields": "id,name", "access_token": token}, timeout=40)
    data = response.json()
    if not response.ok or data.get("id") != FB_PAGE_ID or data.get("name", "").strip().casefold() != FB_PAGE_NAME.casefold():
        raise RuntimeError("No se pudo verificar la página Ojo al Piojo (Facebook HTTP {}).".format(response.status_code))
    print("PAGINA FACEBOOK VERIFICADA: Ojo al Piojo", flush=True)
    return pid, token

def copy_drive_id(file_id, out):
    cmd = ["rclone", "backend", "copyid", "gdrive:", file_id, str(out)]
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if p.returncode or not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("No se pudo descargar ID {} desde gdrive:; revisa permisos de la cuenta rclone de GitHub.".format(file_id))
    print("DRIVE OK:", out.name, "bytes:", out.stat().st_size, flush=True)

def read_state():
    repo = require("GITHUB_REPOSITORY")
    token = require("GH_TOKEN")
    url = f"https://api.github.com/repos/{repo}/contents/{STATE_PATH}"
    response = requests.get(url, headers={"Authorization": "Bearer " + token,
                        "Accept": "application/vnd.github+json"}, timeout=30)
    if response.status_code == 404:
        return {}, None
    response.raise_for_status()
    result = response.json()
    return json.loads(base64.b64decode(result["content"])), result["sha"]

def write_state(state, sha):
    repo = require("GITHUB_REPOSITORY")
    token = require("GH_TOKEN")
    url = f"https://api.github.com/repos/{repo}/contents/{STATE_PATH}"
    body = {
      "message": "Registrar publicación del documental Océano",
      "content": base64.b64encode((json.dumps(state,ensure_ascii=False,indent=2)+"\n").encode()).decode()
    }
    if sha: body["sha"] = sha
    response = requests.put(url, headers={"Authorization": "Bearer "+token,
                 "Accept": "application/vnd.github+json"}, json=body,timeout=30)
    response.raise_for_status()
    return response.json()["content"]["sha"]

def upload_youtube(client,video):
    insert=client.videos().insert(
        part="snippet,status",
        body={
          "snippet":{"title":YT_TITLE, "description":YT_DESCRIPTION,
                     "tags":YT_TAGS, "categoryId":"28","defaultLanguage":"es"},
          "status":{"privacyStatus":"public", "selfDeclaredMadeForKids":False,
                    "containsSyntheticMedia":True},
        },
        media_body=MediaFileUpload(str(video),mimetype="video/mp4",chunksize=8*1024*1024,resumable=True))
    response=None
    while response is None:
        status,response=insert.next_chunk()
        if status: print("YOUTUBE UPLOAD: {:.1f}%".format(status.progress()*100),flush=True)
    video_id=response["id"]
    print("YOUTUBE VIDEO SUBIDO:",video_id,flush=True)
    return video_id

def upload_thumbnail(client,video_id,thumbnail):
    client.thumbnails().set(videoId=video_id,
        media_body=MediaFileUpload(str(thumbnail),mimetype="image/jpeg")).execute()
    print("MINIATURA YOUTUBE SUBIDA",flush=True)

def upload_facebook(page_id,token,video):
    with video.open("rb") as f:
        resp=requests.post(
            f"https://graph-video.facebook.com/{GRAPH_VERSION}/{page_id}/videos",
            data={"access_token":token, "title":FB_TITLE,
                  "description":FB_DESCRIPTION, "published":"true"},
            files={"source":(video.name,f,"video/mp4")},timeout=900)
    try: result=resp.json()
    except ValueError: result={}
    if not resp.ok or not result.get("id"):
        e=result.get("error",{})
        raise RuntimeError("Facebook rechazó la carga: HTTP {}, code {}".format(resp.status_code,e.get("code","-")))
    print("FACEBOOK VIDEO SUBIDO:",result["id"],flush=True)
    return result["id"]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",choices=["verify","publish"],default="verify")
    a=p.parse_args()
    yt=verify_youtube()
    page_id,page_token=verify_facebook()
    state,sha=read_state()
    with tempfile.TemporaryDirectory(prefix="eon_oceano_") as d:
        video=Path(d)/"EON_VERSO_OCEANO_FINAL_1080P.mp4"
        thumb=Path(d)/"EON_VERSO_OCEANO_MINIATURA.jpg"
        copy_drive_id(VIDEO_DRIVE_ID,video)
        copy_drive_id(THUMB_DRIVE_ID,thumb)
        if video.stat().st_size<20_000_000:
            raise RuntimeError("El vídeo descargado parece incompleto.")
        if thumb.stat().st_size>2_000_000:
            raise RuntimeError("Miniatura superior a 2 MB.")
        print("ARCHIVOS VERIFICADOS. mode:",a.mode,flush=True)
        if a.mode=="verify":
            print("VERIFICACION OK: no se publicó contenido.",flush=True)
            return
        if not state.get("youtube_video_id"):
            video_id=upload_youtube(yt,video)
            state["youtube_video_id"]=video_id
            sha=write_state(state,sha)
        else:
            video_id=state["youtube_video_id"]
            print("YOUTUBE YA PUBLICADO EN REGISTRO:",video_id,flush=True)
        if not state.get("youtube_thumbnail_done"):
            upload_thumbnail(yt,video_id,thumb)
            state["youtube_thumbnail_done"]=True
            sha=write_state(state,sha)
        if not state.get("facebook_video_id"):
            fb_id=upload_facebook(page_id,page_token,video)
            state["facebook_video_id"]=fb_id
            sha=write_state(state,sha)
        print("RESULTADO FINAL: YouTube https://www.youtube.com/watch?v=" + video_id)
        print("RESULTADO FINAL: Facebook ID " + state["facebook_video_id"])
        print("FIN PUBLICACION OCEANO",flush=True)

if __name__=="__main__":
    main()
