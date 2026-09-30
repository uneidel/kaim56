# kAIm56 — Jev, the model router's classifier (OpenJev NLI cross-encoder, CPU)
# SPDX-License-Identifier: AGPL-3.0-or-later
FROM python:3.12-slim
RUN pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir transformers==5.17.0 huggingface_hub accelerate sentencepiece tiktoken numpy
COPY server.py /app/server.py
ENV HF_HOME=/hf PYTHONUNBUFFERED=1
EXPOSE 8891
CMD ["python", "/app/server.py"]
