import sys; sys.path.insert(0, '.')
import config
config.CONTENT_DB = config.DATA_DIR / "studio.sqlite3"
from studio import app as S
S.app.run(host="127.0.0.1", port=5100, use_reloader=False)
