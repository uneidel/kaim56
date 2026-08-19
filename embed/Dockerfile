# Embedding-Dienst (semantisches Gedaechtnis). CPU-only: torch aus dem
# CPU-Index, sonst zieht sentence-transformers die CUDA-Raeder auf eine
# GPU-lose Maschine (dieselbe Falle wie beim NeuTTS-Versuch).
FROM python:3.12-slim
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
RUN pip install --no-cache-dir sentence-transformers
# Modell beim Bau mit einbacken -> kein Download beim ersten Start, laeuft
# auch, wenn der Container spaeter kein Internet hat.
ARG EMBED_MODEL=intfloat/multilingual-e5-small
ENV EMBED_MODEL=${EMBED_MODEL}
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}', device='cpu')"
COPY server.py /app/server.py
EXPOSE 8772
CMD ["python3", "-u", "/app/server.py"]
