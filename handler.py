import json
import os
import time
from typing import Any
from urllib.parse import urljoin

import requests
import runpod


ACESTEP_API_URL = os.getenv(
    "ACESTEP_API_URL",
    "http://127.0.0.1:8001"
).rstrip("/")

DJB_UPLOAD_URL = os.getenv(
    "DJB_AI_UPLOAD_URL",
    "https://www.luxartsongs.com/djb-ai-api/upload-audio.php"
).strip()

DJB_UPLOAD_TOKEN = os.getenv(
    "DJB_AI_UPLOAD_TOKEN",
    ""
).strip()

REQUEST_TIMEOUT = 120
GENERATION_TIMEOUT = 1200
UPLOAD_TIMEOUT = 300
POLL_INTERVAL = 2


# ============================================================
# UTILITÀ
# ============================================================

def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def integer(value, default, minimum, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default

    return max(minimum, min(maximum, value))


def string_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        result = []

        for item in value:
            item = clean(item)

            if item:
                result.append(item)

        return result

    value = clean(value)

    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


# ============================================================
# ACE-STEP API
# ============================================================

def ace_post(endpoint: str, payload: dict) -> dict:
    response = requests.post(
        f"{ACESTEP_API_URL}{endpoint}",
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if data.get("code") != 200:
        raise RuntimeError(
            f"ACE_STEP_API_ERROR: {data.get('error')}"
        )

    return data


def create_task(payload: dict) -> str:
    data = ace_post(
        "/release_task",
        payload
    )

    task_data = data.get("data")

    if not isinstance(task_data, dict):
        raise RuntimeError(
            "ACE_STEP_INVALID_RELEASE_RESPONSE"
        )

    task_id = clean(
        task_data.get("task_id")
    )

    if not task_id:
        raise RuntimeError(
            "ACE_STEP_TASK_ID_MISSING"
        )

    return task_id


def query_task(task_id: str) -> dict:
    data = ace_post(
        "/query_result",
        {
            "task_id_list": [task_id]
        }
    )

    results = data.get("data")

    if not isinstance(results, list):
        raise RuntimeError(
            "ACE_STEP_INVALID_QUERY_RESPONSE"
        )

    if not results:
        return {
            "status": "processing"
        }

    result = results[0]

    if not isinstance(result, dict):
        raise RuntimeError(
            "ACE_STEP_INVALID_QUERY_RESULT"
        )

    return result


# ============================================================
# ATTESA GENERAZIONE
# ============================================================

def wait_for_task(task_id: str) -> dict:
    started = time.time()

    while True:

        if time.time() - started > GENERATION_TIMEOUT:
            raise TimeoutError(
                "ACE_STEP_GENERATION_TIMEOUT"
            )

        result = query_task(task_id)

        status = clean(
            result.get("status")
        ).lower()

        if status in {
            "success",
            "succeeded",
            "completed",
            "complete",
            "finished"
        }:
            return result

        if status in {
            "failed",
            "error",
            "cancelled",
            "canceled"
        }:
            raise RuntimeError(
                "ACE_STEP_GENERATION_FAILED: "
                + clean(
                    result.get("error")
                    or result.get("message")
                    or status
                )
            )

        time.sleep(POLL_INTERVAL)


# ============================================================
# ESTRAZIONE RISULTATI ACE-STEP
# ============================================================

def extract_audio_items(result: dict) -> list[dict]:
    candidates = []

    for key in (
        "result",
        "results",
        "output",
        "outputs",
        "data",
        "audio",
        "audios"
    ):
        value = result.get(key)

        if isinstance(value, list):
            candidates.extend(value)

        elif isinstance(value, dict):
            candidates.append(value)

    if not candidates:
        candidates.append(result)

    audio_items = []

    for item in candidates:

        if isinstance(item, str):
            url = clean(item)

            if url:
                audio_items.append(
                    {
                        "url": url
                    }
                )

            continue

        if not isinstance(item, dict):
            continue

        url = clean(
            item.get("audio_url")
            or item.get("audioUrl")
            or item.get("url")
            or item.get("file_url")
            or item.get("fileUrl")
            or item.get("path")
        )

        if not url:
            continue

        audio_items.append(
            {
                "url": url,
                "seed": item.get("seed"),
                "duration": item.get("duration")
            }
        )

    return audio_items


# ============================================================
# DOWNLOAD AUDIO
# ============================================================

def download_audio(audio_url: str) -> bytes:
    if audio_url.startswith("/"):
        audio_url = urljoin(
            ACESTEP_API_URL + "/",
            audio_url.lstrip("/")
        )

    response = requests.get(
        audio_url,
        timeout=UPLOAD_TIMEOUT
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "ACE_STEP_EMPTY_AUDIO"
        )

    return response.content


# ============================================================
# UPLOAD SU ARUBA
# ============================================================

def upload_audio(
    audio_data: bytes,
    variant_index: int
) -> dict:

    if not DJB_UPLOAD_URL:
        raise RuntimeError(
            "DJB_UPLOAD_URL_MISSING"
        )

    if not DJB_UPLOAD_TOKEN:
        raise RuntimeError(
            "DJB_UPLOAD_TOKEN_MISSING"
        )

    filename = (
        f"djb-ai-{int(time.time())}"
        f"-{variant_index}.mp3"
    )

    response = requests.post(
        DJB_UPLOAD_URL,
        headers={
            "Authorization":
                f"Bearer {DJB_UPLOAD_TOKEN}"
        },
        files={
            "file": (
                filename,
                audio_data,
                "audio/mpeg"
            )
        },
        data={
            "variant": str(variant_index)
        },
        timeout=UPLOAD_TIMEOUT
    )

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "DJB_UPLOAD_INVALID_JSON"
        ) from exc

    if not data.get("success"):
        raise RuntimeError(
            "DJB_UPLOAD_FAILED: "
            + clean(
                data.get("message")
                or data.get("error")
            )
        )

    public_url = clean(
        data.get("url")
        or data.get("audio_url")
        or (
            data.get("data", {}).get("url")
            if isinstance(data.get("data"), dict)
            else ""
        )
    )

    if not public_url:
        raise RuntimeError(
            "DJB_UPLOAD_URL_MISSING_IN_RESPONSE"
        )

    return {
        "url": public_url,
        "filename": clean(
            data.get("filename")
            or filename
        )
    }


# ============================================================
# PUBBLICAZIONE RISULTATI
# ============================================================

def publish_results(
    ace_result: dict,
    max_variants: int
) -> list[dict]:

    audio_items = extract_audio_items(
        ace_result
    )

    if not audio_items:
        raise RuntimeError(
            "ACE_STEP_NO_AUDIO_RESULTS"
        )

    published = []

    for index, item in enumerate(
        audio_items[:max_variants],
        start=1
    ):

        audio_url = clean(
            item.get("url")
        )

        if not audio_url:
            continue

        audio_data = download_audio(
            audio_url
        )

        uploaded = upload_audio(
            audio_data,
            index
        )

        published.append(
            {
                "variant":
                    "A" if index == 1 else "B",

                "url":
                    uploaded["url"],

                "filename":
                    uploaded["filename"],

                "seed":
                    item.get("seed"),

                "duration":
                    item.get("duration")
            }
        )

    if not published:
        raise RuntimeError(
            "DJB_NO_PUBLISHED_AUDIO"
        )

    return published


# ============================================================
# COSTRUZIONE PROMPT ARTISTICO
# ============================================================

def build_final_prompt(data: dict) -> str:
    user_prompt = clean(
        data.get("prompt")
    )

    genres = string_list(
        data.get("genres")
        or data.get("genre")
    )

    moods = string_list(
        data.get("moods")
        or data.get("mood")
    )

    instruments = string_list(
        data.get("instruments")
    )

    voice_characters = string_list(
        data.get("voiceCharacters")
    )

    deliveries = string_list(
        data.get("deliveries")
    )

    voice = clean(
        data.get("voice")
    )

    vocal_type = clean(
        data.get("vocalType")
    )

    vocal_tone = clean(
        data.get("vocalTone")
    )

    male_range = clean(
        data.get("maleRange")
    )

    female_range = clean(
        data.get("femaleRange")
    )

    tempo = clean(
        data.get("tempo")
    )

    sections = []

    if genres:
        sections.append(
            "GENRE: " + ", ".join(genres)
        )

    if moods:
        sections.append(
            "MOOD: " + ", ".join(moods)
        )

    vocal_parts = [
        value
        for value in (
            voice,
            vocal_type
        )
        if value
    ]

    if vocal_parts:
        sections.append(
            "VOCALS: "
            + ", ".join(vocal_parts)
        )

    if voice_characters:
        sections.append(
            "VOICE CHARACTER: "
            + ", ".join(voice_characters)
        )

    if vocal_tone:
        sections.append(
            "VOCAL TONE: "
            + vocal_tone
        )

    if male_range:
        sections.append(
            "MALE VOCAL RANGE: "
            + male_range
        )

    if female_range:
        sections.append(
            "FEMALE VOCAL RANGE: "
            + female_range
        )

    if deliveries:
        sections.append(
            "VOCAL DELIVERY: "
            + ", ".join(deliveries)
        )

    if instruments:
        sections.append(
            "INSTRUMENTS: "
            + ", ".join(instruments)
        )

    if tempo:
        sections.append(
            "TEMPO CHARACTER: "
            + tempo
        )

    if user_prompt:
        sections.append(
            "ARRANGEMENT AND MUSICAL DIRECTION: "
            + user_prompt
        )

    return "\n".join(sections)


# ============================================================
# HANDLER RUNPOD
# ============================================================

def handler(job):
    try:

        data = job.get("input", {})

        if not isinstance(data, dict):
            raise ValueError(
                "INVALID_INPUT"
            )

        lyrics = clean(
            data.get("lyrics")
        )

        prompt = build_final_prompt(
            data
        )

        if not prompt and not lyrics:
            raise ValueError(
                "PROMPT_AND_LYRICS_EMPTY"
            )

        duration = integer(
            data.get(
                "durationSeconds",
                data.get(
                    "duration",
                    270
                )
            ),
            270,
            60,
            360
        )

        bpm = integer(
            data.get("bpm"),
            120,
            60,
            200
        )

        variants = integer(
            data.get("variants"),
            2,
            1,
            2
        )

        key = clean(
            data.get("key")
        )

        key_mode = clean(
            data.get("keyMode")
        )

        key_scale = ""

        if key and key.lower() != "auto":

            if (
                key_mode
                and key_mode.lower()
                != "auto"
            ):
                key_scale = (
                    f"{key} {key_mode}"
                )
            else:
                key_scale = key

        vocal_language = clean(
            data.get("vocal_language")
        )

        if not vocal_language:
            vocal_language = "en"

        ace_payload = {
            "prompt": prompt,
            "lyrics": lyrics,
            "thinking": True,
            "use_format": True,
            "model": "acestep-v15-turbo",
            "audio_format": "mp3",
            "audio_duration": duration,
            "bpm": bpm,
            "batch_size": variants,
            "use_random_seed": True
        }

        if key_scale:
            ace_payload[
                "key_scale"
            ] = key_scale

        if vocal_language:
            ace_payload[
                "vocal_language"
            ] = vocal_language

        print(
            "DJB ACE-Step request:"
        )

        print(
            json.dumps(
                {
                    "audio_duration":
                        duration,
                    "bpm":
                        bpm,
                    "batch_size":
                        variants,
                    "key_scale":
                        key_scale,
                    "vocal_language":
                        vocal_language
                },
                ensure_ascii=False
            )
        )

        task_id = create_task(
            ace_payload
        )

        print(
            f"ACE-Step task created: {task_id}"
        )

        result = wait_for_task(
            task_id
        )

        print(
            f"ACE-Step task completed: {task_id}"
        )

        songs = publish_results(
            result,
            variants
        )

        return {
            "success": True,
            "status": "COMPLETED",
            "task_id": task_id,
            "songs": songs
        }

    except Exception as exc:

        print(
            "DJB WORKER ERROR:",
            repr(exc)
        )

        return {
            "success": False,
            "status": "FAILED",
            "error": str(exc)
        }


# ============================================================
# AVVIO RUNPOD SERVERLESS
# ============================================================

runpod.serverless.start(
    {
        "handler": handler
    }
)
