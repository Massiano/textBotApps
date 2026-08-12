# CineTot (Flask + OpenRouter)

Language-learning game: AI writes a simplified movie synopsis in the target
language, you guess the movie, click words for definitions.

## Local run

pip install -r requirements.txt --break-system-packages
cp .env.example .env   # fill in OPENROUTER_API_KEY, set your own ADMIN_EXPORT_PATH
export $(cat .env | xargs)
python app.py          # http://localhost:5000

## Deploy (Render / Railway)

Both read the Procfile (`web: gunicorn app:app`) automatically.
Set these env vars in the platform dashboard:

- OPENROUTER_API_KEY
- SITE_URL       (your deployed URL, used in OpenRouter's HTTP-Referer header)
- SITE_NAME
- ADMIN_EXPORT_PATH   (pick a random slug — this is the only protection on /admin/<path>)

Disk on both platforms is ephemeral on redeploy. `data/backups.json` is a
convenience snapshot of per-visitor language/known-words settings, not a
database — visit `/admin/<ADMIN_EXPORT_PATH>` before redeploying if you want
a copy.

## Notes

- No login system. Visitors are told apart by a server-set cookie; IP and
  user-agent are stored alongside as soft fingerprint metadata, not used as
  the lookup key.
- Image generation ("Visualise It" in the original) is dropped for 1.0 —
  no reliable free OpenRouter image model exists yet. `services/openrouter.py`
  is structured so an image function can be added later without touching
  the rest of the app.
- Text generation tries `PREFERRED_MODELS` in `services/openrouter.py` in
  order, all via OpenRouter's structured-output (json_schema) mode, falling
  through to `openrouter/free` if all named models fail.
