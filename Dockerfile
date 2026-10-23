# Container for Hugging Face Spaces (Docker SDK). HF serves it over HTTPS and forwards to port 7860.
FROM python:3.13-slim

# Spaces run the container as user 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

# OCR models and the browser runtime are not stored in git: fetch them at build time
RUN python scripts/setup_assets.py

# Fresh ?v= numbers on every build so phones don't keep running old cached JavaScript
RUN sed -i "s/?v=[0-9]*/?v=$(date +%s)/g" scanner/frontend/index.html scanner/frontend/main.js scanner/frontend/worker.js

EXPOSE 7860
# serve.py adds the cross-origin isolation headers (multi-threaded OCR); HF provides the HTTPS
CMD ["streamlit", "run", "serve.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
