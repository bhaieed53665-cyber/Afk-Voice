FROM python:3.11-slim

WORKDIR /app

# discord-native-voice declares discord.py-self>=2.2.0, but only 2.2.0a0 (GitHub master)
# exists, so pip's resolver rejects it. Install it separately with --no-deps.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir --no-deps discord-native-voice

COPY . .

# -u = unbuffered stdout so logs appear immediately
CMD ["python", "-u", "bot.py"]
