# Embedding service (semantic memory). CPU-only: torch from the
# CPU index, otherwise sentence-transformers pulls the CUDA wheels onto a
# GPU-less machine (the same trap as with the NeuTTS attempt).
FROM python:3.12-slim
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
RUN pip install --no-cache-dir sentence-transformers
# Bake the model in at build time -> no download on first start, works even
# if the container later has no internet.
ARG EMBED_MODEL=intfloat/multilingual-e5-small
ENV EMBED_MODEL=${EMBED_MODEL}
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}', device='cpu')"
COPY server.py /app/server.py
EXPOSE 8772
CMD ["python3", "-u", "/app/server.py"]
