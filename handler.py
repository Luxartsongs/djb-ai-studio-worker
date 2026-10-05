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
