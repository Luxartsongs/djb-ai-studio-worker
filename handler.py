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
    data = ace_post("/release_task", payload)

    task_data = data.get("data")

    if not isinstance(task_data, dict):
        raise RuntimeError(
            "ACE_STEP_INVALID_RELEASE_RESPONSE"
        )

    task_id = clean(task_data.get("task_id"))

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
            "status": 0,
            "result": "[]"
        }

    result = results[0]

    if not isinstance(result, dict):
        raise RuntimeError(
            "ACE_STEP_INVALID_TASK_RESULT"
        )

    return result


def wait_for_task(task_id: str) -> list:
    started = time.monotonic()

    while True:
        if time.monotonic() - started > GENERATION_TIMEOUT:
            raise TimeoutError(
                "ACE_STEP_GENERATION_TIMEOUT"
            )

        task = query_task(task_id)

        try:
            status = int(task.get("status", 0))
        except (TypeError, ValueError):
            status = 0

        if status == 0:
            time.sleep(POLL_INTERVAL)
            continue

        if status == 2:
            raise RuntimeError(
                f"ACE_STEP_GENERATION_FAILED: "
                f"{task.get('result', '')}"
            )

        if status != 1:
            raise RuntimeError(
                f"ACE_STEP_UNKNOWN_STATUS_{status}"
            )

        raw_result = task.get("result", "[]")

        if isinstance(raw_result, str):
            try:
                result = json.loads(raw_result)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "ACE_STEP_RESULT_INVALID_JSON"
                ) from exc
        else:
            result = raw_result

        if not isinstance(result, list):
            raise RuntimeError(
                "ACE_STEP_RESULT_NOT_LIST"
            )

        if not result:
            raise RuntimeError(
                "ACE_STEP_RESULT_EMPTY"
            )

        return result


def download_audio(file_reference: str):
    reference = clean(file_reference)

    if not reference:
        raise RuntimeError(
            "ACE_STEP_AUDIO_REFERENCE_MISSING"
        )

    if reference.startswith(("http://", "https://")):
        audio_url = reference
    else:
        audio_url = urljoin(
            ACESTEP_API_URL + "/",
            reference.lstrip("/")
        )

    response = requests.get(
        audio_url,
        timeout=UPLOAD_TIMEOUT
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "ACE_STEP_AUDIO_EMPTY"
        )

    content_type = (
        response.headers
        .get("Content-Type", "audio/mpeg")
        .split(";")[0]
        .strip()
    )

    return response.content, content_type


def upload_to_aruba(
    audio: bytes,
    content_type: str,
    variant: int
) -> dict:

    if not DJB_UPLOAD_TOKEN:
        raise RuntimeError(
            "DJB_AI_UPLOAD_TOKEN_NOT_CONFIGURED"
        )

    extension_map = {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/flac": "flac",
        "audio/x-flac": "flac",
        "audio/ogg": "ogg",
        "application/ogg": "ogg",
    }

    extension = extension_map.get(
        content_type.lower(),
        "mp3"
    )

    filename = (
        f"djb-ai-{int(time.time())}-"
        f"{variant}.{extension}"
    )

    response = requests.post(
        DJB_UPLOAD_URL,
        headers={
            "Authorization":
                f"Bearer {DJB_UPLOAD_TOKEN}"
        },
        files={
            "audio": (
                filename,
                audio,
                content_type
            )
        },
        timeout=UPLOAD_TIMEOUT
    )

    if not response.ok:
        raise RuntimeError(
            f"ARUBA_UPLOAD_HTTP_"
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "ARUBA_UPLOAD_INVALID_JSON"
        ) from exc

    if data.get("success") is not True:
        raise RuntimeError(
            f"ARUBA_UPLOAD_FAILED: "
            f"{data.get('message') or data.get('error')}"
        )

    result = data.get("data")

    if not isinstance(result, dict):
        raise RuntimeError(
            "ARUBA_UPLOAD_INVALID_DATA"
        )

    if not clean(result.get("audio_url")):
        raise RuntimeError(
            "ARUBA_UPLOAD_URL_MISSING"
        )

    return result


def publish_results(
    results: list,
    requested_variants: int
) -> list:

    published = []

    for number, item in enumerate(
        results[:requested_variants],
        start=1
    ):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"ACE_STEP_VARIANT_{number}_INVALID"
            )

        file_reference = clean(
            item.get("file")
        )

        if not file_reference:
            raise RuntimeError(
                f"ACE_STEP_VARIANT_{number}_FILE_MISSING"
            )

        audio, content_type = download_audio(
            file_reference
        )

        uploaded = upload_to_aruba(
            audio,
            content_type,
            number
        )

        published.append(
            {
                "variant": number,
                "audio_url": uploaded["audio_url"],
                "filename": uploaded.get("filename"),
                "size": uploaded.get("size"),
            }
        )

    if not published:
        raise RuntimeError(
            "NO_AUDIO_VARIANTS_CREATED"
        )

    return published


def handler(event):
    try:
        job_input = event.get("input")

        if not isinstance(job_input, dict):
            return {
                "success": False,
                "error": "INVALID_INPUT"
            }

        prompt = clean(
            job_input.get("prompt")
        )

        lyrics = clean(
            job_input.get("lyrics")
        )

        if not prompt:
            return {
                "success": False,
                "error": "PROMPT_REQUIRED"
            }

        duration = integer(
            job_input.get("duration"),
            60,
            10,
            600
        )

        bpm = integer(
            job_input.get("bpm"),
            120,
            30,
            300
        )

        variants = integer(
            job_input.get("variants"),
            3,
            1,
            8
        )

        key_scale = clean(
            job_input.get("key")
        )

        if key_scale.lower() in {
            "",
            "auto",
            "automatic",
            "automatico"
        }:
            key_scale = ""

        language = clean(
            job_input.get("vocal_language")
        ) or "en"

        payload = {
            "prompt": prompt,
            "lyrics": lyrics,
            "thinking": True,
            "use_format": True,
            "model": "acestep-v15-turbo",
            "audio_format": "mp3",
            "audio_duration": duration,
            "bpm": bpm,
            "batch_size": variants,
            "use_random_seed": True,
        }

        if key_scale:
            payload["key_scale"] = key_scale

        if lyrics:
            payload["vocal_language"] = language

        task_id = create_task(payload)

        results = wait_for_task(task_id)

        variants_result = publish_results(
            results,
            variants
        )

        return {
            "success": True,
            "task_id": task_id,
            "count": len(variants_result),
            "variants": variants_result,
        }

    except requests.RequestException as exc:
        return {
            "success": False,
            "error": "NETWORK_ERROR",
            "details": str(exc),
        }

    except TimeoutError as exc:
        return {
            "success": False,
            "error": "GENERATION_TIMEOUT",
            "details": str(exc),
        }

    except RuntimeError as exc:
        return {
            "success": False,
            "error": "GENERATION_ERROR",
            "details": str(exc),
        }

    except Exception as exc:
        return {
            "success": False,
            "error": "UNEXPECTED_ERROR",
            "details": f"{type(exc).__name__}: {exc}",
        }


if __name__ == "__main__":
    runpod.serverless.start(
        {
            "handler": handler
        }
    )
