#!/usr/bin/env python3
"""EON-Verso 1.4: publicar un Short FINAL por ejecución, sin duplicados."""
import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from eon_publish import (
    verify_youtube, verify_facebook, verify_tiktok, verify_cloudinary,
    rclone_file, youtube_upload, facebook_upload, cloudinary_upload,
    buffer_graphql, gh_state,
)

VERSION = "1.4"
FOLDERS = {
    "perros": "1Bbpbk14u2ibb4MhlEoRHl9YT2gEcPOpK",
    "suenos": "17N8W4tDH4WEsoqMsKHgPHTrfOocwipfk",
    "oceano": "1kM1S33Fzur0Hd61pjQssH5_KC0PGsGW7",
}
STEMS = {"perros": "Perros", "suenos": "Suenos", "oceano": "Oceano"}
INITIAL_IDS = {
    "perros": [
        "19oM6zRvzmWoxvpBrsFYKeha9_irpmfbL",
        "1FraKmlO55tIN2CjrnTM1UqO4pxcB5MIF",
        "17h4GOqJAceZycymHcMLq-o1r6Wag1riq",
        "1UEXg_3fJooMiJWf2BxIImnt1L1ytFscp",
        "1Z4OGO3W1knCIj3NEpNnRn2xxPmLZQ2Xb",
        "1d0NOaAa7Gihlh3FSe2eGnbGu2rCrWJcQ",
    ],
    "suenos": [
        "1OvwmI7XnpkOizncHGh-rHKh9JOTLcZ-Y",
        "1CpOH1paGT4JUe9QNmtrlCZ7OK0sa8nuk",
        "19jvCq18oeCZHJYI5z5HdF3iNZqDfIG6U",
        "1Ish45CI6nvxW0USbpsNNRIuIag-Qudsw",
        "1t4D5VYCQSchdxcw-D9wtRABNj77H63oA",
        "1GhLjUG6Y69qAa7pS4wtvu73-TdiV7o2G",
        "1otxDuz-1aWOn3GV92NbDbXImQKT1od2j",
        "1NZZjiFDuaP4v20fRt6FDc_fHca1zNCkN",
    ],
    "oceano": [
        "1P0Ch0AkIO5ZNLztiW_oodJeugPiqik1n",
        "15MKz9RSYu6WVI7kr0nEvZlQ0QEGSCMpB",
        "1sq-JCIQkzIz5RFyCcaf4j--_G04-w4Sx",
        "1xNX6hFMk3teh8SzHJ2-0q-W6WRNfCJ8q",
        "1rzUUvbtSmqmcegW6jIvStYUZuRg1gWHf",
        "1zFOASIODzPoiCiW8pHX0u-ZHx6hFGzaM",
        "1b2ewm0-HFdKySWV2qO3ldS-YTvEaR_S4",
        "1bRb4RZP0gJjqZcpYSh9de_vzIx0B5qUL",
        "12HIOmzwhsEtkBBhlMYh0tNyWcPGTiQFZ",
    ],
}
TOPIC_TITLES = {
    "perros": "¿Por qué los perros inclinan la cabeza?",
    "suenos": "¿Por qué soñamos? Un misterio de nuestro cerebro",
    "oceano": "Los misterios de las profundidades del océano",
}
TOPIC_TEXTS = {
    "perros": "Los perros nos sorprenden con sus gestos y su manera de escucharnos. Descubre una curiosidad sobre su comportamiento.",
    "suenos": "Mientras dormimos, nuestro cerebro crea experiencias sorprendentes. Descubre una curiosidad sobre los sueños.",
    "oceano": "Bajo la superficie del océano existen criaturas y fenómenos increíbles. Descubre otra curiosidad de las profundidades.",
}
HASHTAGS = {
    "perros": "#Perros #CuriosidadesDePerros #ComportamientoCanino #Animales #EONVerso",
    "suenos": "#Sueños #PorQueSoñamos #Cerebro #Curiosidades #EONVerso",
    "oceano": "#OceanoProfundo #VidaMarina #Curiosidades #Ciencia #EONVerso",
}
TAGS = {
    "perros": ["perros","por qué inclinan la cabeza","comportamiento canino","curiosidades de perros","EON-Verso"],
    "suenos": ["por qué soñamos","sueños","cerebro","curiosidades","EON-Verso"],
    "oceano": ["océano","océano profundo","vida marina","curiosidades marinas","EON-Verso"],
}
ORDER = ["perros", "suenos", "oceano"]
ALL_PLATFORMS = ("youtube","facebook","tiktok")


def initial_files():
    files = {}
    for topic, ids in INITIAL_IDS.items():
        for number, file_id in enumerate(ids, 1):
            key = f"{topic}-{number:02d}"
            files[key] = {
                "id": file_id,
                "filename": f"EON_Verso_{STEMS[topic]}_Short_{number:02d}_FINAL.mp4",
                "topic": topic,
                "number": number,
            }
    return files


def discover():
    """Leer carpetas reales. Conservar copia verificada si Drive no permite listar."""
    candidates = initial_files()
    for topic, folder_id in FOLDERS.items():
        command = [
            "rclone", "lsjson", "gdrive:", "--files-only",
            "--drive-root-folder-id", folder_id,
        ]
        run = subprocess.run(command, text=True, capture_output=True, timeout=120)
        if run.returncode:
            print(f"AVISO: no se pudo enumerar {topic}; usar lista FINAL verificada.", flush=True)
            continue
        try:
            items = json.loads(run.stdout)
        except ValueError:
            print(f"AVISO: listado Drive inválido en {topic}.", flush=True)
            continue
        count = 0
        for item in items:
            name = item.get("Name", "")
            match = re.fullmatch(
                rf"EON_Verso_{STEMS[topic]}_Short_(\d+)_FINAL(?:_V\d+)?\.mp4",
                name, flags=re.IGNORECASE,
            )
            if not match or not item.get("ID"):
                continue
            number = int(match.group(1))
            key = f"{topic}-{number:02d}"
            candidates[key] = {
                "id": item["ID"], "filename": name,
                "topic": topic, "number": number,
            }
            count += 1
        print(f"DRIVE {topic}: {count} MP4 FINAL encontrados.", flush=True)
    return candidates


def metadata_for(entry):
    topic = entry["topic"]
    number = entry["number"]
    title = TOPIC_TITLES[topic]
    # Título real verificado visualmente en el primer vídeo.
    if topic != "perros" or number != 1:
        title = f"{title} | Curiosidad {number}"
    intro = TOPIC_TEXTS[topic]
    yt = (intro + "\n\nMira más documentales de ciencia y curiosidades en @eonverso. "
          "Suscríbete a EON-Verso en YouTube.\n\n" + HASHTAGS[topic])
    fb = (title + "\n\n" + intro +
          "\n\nSigue nuestra página para más curiosidades.\n\n" + HASHTAGS[topic])
    return {
        "title": title[:100], "youtube_description": yt,
        "facebook_description": fb, "tags": TAGS[topic],
        "tiktok_description": (title + "\n" + intro +
                               "\nMás curiosidades en EON-Verso (YouTube).\n" + HASHTAGS[topic]),
    }


def choose(files, state, mode):
    if mode == "first":
        candidate = files.get("perros-01")
        if candidate and any(p not in state.get("perros-01", {}) for p in ALL_PLATFORMS):
            return "perros-01", candidate
    pending = []
    for key, entry in files.items():
        posted = state.get(key, {})
        if any(p not in posted for p in ALL_PLATFORMS):
            pending.append((key, entry))
    if not pending:
        return None, None
    # Primero terminar publicaciones parcialmente realizadas, sin crear duplicados.
    partial = [x for x in pending if any(p in state.get(x[0], {}) for p in ALL_PLATFORMS)]
    if partial:
        return sorted(partial, key=lambda x: x[1]["number"])[0]
    last = state.get("_auto", {}).get("last_topic", "oceano")
    start = (ORDER.index(last) + 1) % len(ORDER) if last in ORDER else 0
    for offset in range(len(ORDER)):
        topic = ORDER[(start + offset) % len(ORDER)]
        available = sorted(
            (x for x in pending if x[1]["topic"] == topic),
            key=lambda x: x[1]["number"],
        )
        if available:
            return available[0]
    return None, None


def tiktok_now(channel, video_url, meta):
    """Compartir ahora mediante Buffer; el envío final puede requerir procesamiento."""
    mutation = """mutation EonNow($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id dueAt status } }
        ... on MutationError { message }
      }
    }"""
    payload = {
        "channelId": channel,
        "text": meta["tiktok_description"][:2000],
        "schedulingType": "automatic",
        "mode": "shareNow",
        "assets": [{"video": {"url": video_url}}],
    }
    result = buffer_graphql(mutation, {"input": payload}).get("createPost") or {}
    post = result.get("post") or {}
    if not post.get("id"):
        raise RuntimeError("Buffer no aceptó el Short: " + str(result.get("message", "sin motivo"))[:160])
    return post


def run_once(mode):
    print(f"EON SHORTS AUTOMÁTICOS v{VERSION}, modo={mode}", flush=True)
    state, sha = gh_state("read")
    files = discover()
    key, entry = choose(files, state, mode)
    if key is None:
        print("SIN SHORTS NUEVOS: no hay publicaciones pendientes.", flush=True)
        return
    pending = [p for p in ALL_PLATFORMS if p not in state.get(key, {})]
    print(f"SHORT SELECCIONADO: {key}; pendientes: {', '.join(pending)}", flush=True)
    # Todas las cuentas se comprueban antes de la primera subida.
    youtube = verify_youtube() if "youtube" in pending else None
    facebook = verify_facebook() if "facebook" in pending else None
    tiktok = verify_tiktok() if "tiktok" in pending else None
    if "tiktok" in pending:
        verify_cloudinary()
    meta = metadata_for(entry)
    with tempfile.TemporaryDirectory(prefix="eon_shorts_auto_") as temp:
        video = Path(temp) / entry["filename"]
        rclone_file(entry["id"], video)
        if video.stat().st_size < 100_000:
            raise RuntimeError("Archivo MP4 demasiado pequeño: cancelado.")
        print(f"ARCHIVO FINAL: {video.name} ({video.stat().st_size} bytes)", flush=True)
        for platform in pending:
            if platform == "youtube":
                post_id = youtube_upload(youtube, video, meta)
                url = "https://www.youtube.com/watch?v=" + post_id
                status = "subido"
            elif platform == "facebook":
                post_id = facebook_upload(facebook[0], facebook[1], video, meta)
                url = "https://www.facebook.com/watch/?v=" + post_id
                status = "subido"
            else:
                host_url = cloudinary_upload(key, video)
                result = tiktok_now(tiktok, host_url, meta)
                post_id = result["id"]
                status = "enviado_a_buffer_" + str(result.get("status", "pendiente"))
                url = "https://publish.buffer.com/"
            state.setdefault(key, {})[platform] = {
                "id": post_id, "url": url, "status": status,
                "drive_file_id": entry["id"],
                "utc": datetime.now(timezone.utc).isoformat(),
            }
            try:
                sha = gh_state("write", state, sha)
            except Exception as exc:
                raise RuntimeError(
                    f"ALERTA: {platform} aceptó {key} ({post_id}), "
                    "pero falló registrar la subida. No reiniciar sin revisar."
                ) from exc
            print(f"RESULTADO {platform.upper()}: {status} -> {url}", flush=True)
    if all(p in state.get(key, {}) for p in ALL_PLATFORMS):
        state["_auto"] = {
            "last_topic": entry["topic"], "last_short": key,
            "last_utc": datetime.now(timezone.utc).isoformat(),
        }
        sha = gh_state("write", state, sha)
        print(f"TERMINADO {key}: tres plataformas procesadas.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["first","scheduled"], default="scheduled")
    args = parser.parse_args()
    run_once(args.mode)
