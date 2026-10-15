#!/usr/bin/env python3
"""Facebook Ojo al Piojo: publicar documentales Sueños y Perros con control de duplicados."""
import argparse
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests

FB_PAGE_ID = "111079017290300"
FB_PAGE_NAME = "Ojo al Piojo"
GRAPH = "v25.0"
STATE_PATH = "eon-facebook-documentales-state.json"

VIDEOS = {
    "suenos": {
        "drive_id": "1Yh_scX-fq185q1l2KLNNvKLhgDFfFSuY",
        "filename": "EON_VERSO_SUENOS_DOCUMENTAL_FINAL_1080P.mp4",
        "title": "¿Por qué soñamos? Los secretos que esconde nuestro cerebro",
        "description": """💤 ¿Por qué soñamos? ¿Qué pasa en nuestro cerebro mientras dormimos?

A veces soñamos cosas imposibles, despertamos convencidos de que todo fue real o incluso descubrimos que estamos soñando. ¿Por qué ocurre?

En este documental exploramos los sueños, la actividad del cerebro durante el descanso, los sueños lúcidos y algunos fenómenos que pueden hacernos confundir el sueño con la realidad.

🌙 Mira el documental completo y cuéntanos: ¿alguna vez supiste que estabas soñando sin despertarte?

👍 Sigue Ojo al Piojo para más curiosidades sobre nuestro cerebro, la naturaleza y la ciencia.

Aviso: El documental utiliza ilustraciones y narración creadas con herramientas de inteligencia artificial. Las imágenes son representaciones educativas, no grabaciones reales de experimentos.

#PorQueSonamos #SuenosLucidos #Curiosidades #Cerebro #Ciencia""",
    },
    "perros": {
        "drive_id": "1pEPKtrziBV3D1hSd0a3lm8pLhs22U2IS",
        "filename": "EON_VERSO_PERROS_DOCUMENTAL_FINAL_1080P.mp4",
        "title": "¿Por qué los perros inclinan la cabeza cuando les hablamos?",
        "description": """🐶 ¿Alguna vez le hablaste a un perro y, de repente, inclinó la cabeza hacia un lado?

Es uno de sus gestos más curiosos. Pero ¿qué significa realmente? ¿Está escuchando mejor, observando nuestra cara o prestando atención a algo que aprendió?

En este documental descubrimos posibles explicaciones científicas sobre los oídos, la atención y cómo los perros reaccionan a nuestra voz y a las palabras que reconocen. No todos los perros lo hacen, y eso también tiene explicaciones posibles.

🐕 Mira el documental completo y cuéntanos: ¿tu perro inclina la cabeza cuando le hablas?

👍 Sigue Ojo al Piojo para más historias sorprendentes sobre animales y ciencia.

Aviso: El documental utiliza ilustraciones y narración creadas con herramientas de inteligencia artificial. Las imágenes son representaciones educativas, no grabaciones reales de experimentos.

#Perros #CuriosidadesDePerros #PorQueInclinanLaCabeza #Animales #Ciencia""",
    },
}

def require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("Falta configurar el secreto/variable " + name)
    return value

def verify_facebook():
    page_id = require("FACEBOOK_PAGE_ID")
    if page_id != FB_PAGE_ID:
        raise RuntimeError("FACEBOOK_PAGE_ID no corresponde a Ojo al Piojo. Cancelado.")
    token = require("FACEBOOK_PAGE_TOKEN")
    result = requests.get(
        f"https://graph.facebook.com/{GRAPH}/{page_id}",
        params={"fields": "id,name", "access_token": token}, timeout=35)
    data = result.json()
    if not result.ok or data.get("id") != FB_PAGE_ID or data.get("name", "").strip().casefold() != FB_PAGE_NAME.casefold():
        raise RuntimeError("No se verificó la página Ojo al Piojo: HTTP {}".format(result.status_code))
    print("FACEBOOK OK: Ojo al Piojo", flush=True)
    return page_id, token

def drive_copy(file_id, path):
    run = subprocess.run(
        ["rclone", "backend", "copyid", "gdrive:", file_id, str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    if run.returncode or not path.exists() or path.stat().st_size < 5_000_000:
        raise RuntimeError("Error descargando el documental desde Google Drive (ID {}).".format(file_id))
    print("VIDEO DRIVE OK: {} ({} bytes)".format(path.name, path.stat().st_size), flush=True)

def gh_api():
    repo = require("GITHUB_REPOSITORY")
    return "https://api.github.com/repos/{}/contents/{}".format(repo, STATE_PATH), {
        "Authorization": "Bearer " + require("GH_TOKEN"),
        "Accept": "application/vnd.github+json",
    }

def read_state():
    url, headers = gh_api()
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 404:
        return {}, None
    r.raise_for_status()
    data = r.json()
    return json.loads(base64.b64decode(data["content"])), data["sha"]

def save_state(state, sha):
    url, headers = gh_api()
    body = {"message": "Registrar documentales publicados en Ojo al Piojo",
            "content": base64.b64encode((json.dumps(state, indent=2, ensure_ascii=False)+"\n").encode()).decode()}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["content"]["sha"]

def page_video_titles(page_id, token):
    try:
        r = requests.get(f"https://graph.facebook.com/{GRAPH}/{page_id}/videos",
             params={"fields": "id,title,created_time", "limit": 100,
                     "access_token":token}, timeout=40)
        data = r.json()
        if not r.ok or not isinstance(data.get("data"), list):
            print("AVISO: el listado de vídeos anteriores no está disponible; se usará el registro de GitHub para prevenir duplicados.",flush=True)
            return None
        print("VIDEOS EXISTENTES CONSULTADOS:", len(data["data"]), flush=True)
        return data["data"]
    except (ValueError, requests.RequestException):
        print("AVISO: no se pudo consultar los vídeos anteriores; se usará el registro de GitHub.",flush=True)
        return None

def upload_facebook(page_id, token, path, video):
    with path.open("rb") as f:
        r = requests.post(
          f"https://graph-video.facebook.com/{GRAPH}/{page_id}/videos",
          data={"access_token": token, "title": video["title"],
                "description": video["description"], "published": "true"},
          files={"source": (path.name, f, "video/mp4")}, timeout=900)
    try:
        payload=r.json()
    except ValueError:
        payload={}
    if not r.ok or not payload.get("id"):
        error=payload.get("error",{})
        raise RuntimeError("Facebook no aceptó el vídeo (HTTP {}, código {}).".format(r.status_code,error.get("code","-")))
    return payload["id"]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode", choices=["verify", "publish"], default="verify")
    args=p.parse_args()
    page_id, token=verify_facebook()
    state, sha=read_state()
    existing=page_video_titles(page_id,token)
    with tempfile.TemporaryDirectory(prefix="fb_eon_docs_") as temporary:
        for key, video in VIDEOS.items():
            if state.get(key, {}).get("id"):
                print(key.upper(), "YA EN REGISTRO:", state[key]["id"], flush=True)
                continue
            matches=[v for v in (existing or []) if v.get("title","").strip().casefold()==video["title"].strip().casefold()]
            if matches:
                print(key.upper(), "PARECE YA PUBLICADO EN FACEBOOK, no duplicar:",matches[0]["id"],flush=True)
                if args.mode=="publish":
                    state[key]={"id":matches[0]["id"],"title":video["title"],"source_drive_id":video["drive_id"]}
                    sha=save_state(state,sha)
                continue
            path=Path(temporary)/video["filename"]
            drive_copy(video["drive_id"], path)
            if args.mode=="verify":
                print(key.upper(), "LISTO PARA PUBLICAR. No se publicó nada.",flush=True)
                continue
            result_id=upload_facebook(page_id,token,path,video)
            state[key]={"id":result_id,"title":video["title"],"source_drive_id":video["drive_id"]}
            sha=save_state(state,sha)
            print("PUBLICADO",key.upper(),"https://www.facebook.com/watch/?v="+result_id,flush=True)
    print("FIN MODO:",args.mode,flush=True)

if __name__=="__main__":
    main()
