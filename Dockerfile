FROM ghcr.io/ace-step/ace-step-1.5:latest

USER root

WORKDIR /app

RUN uv pip install --python /app/.venv/bin/python \
    runpod \
    requests

COPY handler.py /app/djb/handler.py
COPY start.sh /app/djb/start.sh

RUN chmod +x /app/djb/start.sh

ENV ACESTEP_API_HOST=127.0.0.1
ENV ACESTEP_API_PORT=8001
ENV ACESTEP_API_WORKERS=1

ENV ACESTEP_CONFIG_PATH=acestep-v15-turbo
ENV ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-1.7B
ENV ACESTEP_LM_BACKEND=pt
ENV ACESTEP_INIT_LLM=true

ENV TOKENIZERS_PARALLELISM=false

ENTRYPOINT ["/app/djb/start.sh"]
