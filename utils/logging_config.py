import logging
import os
import sys

def setup_logging():
    """Configure logging for the application.

    Writes to APP_DATA_DIR/app.log (defaults to ./app.log when the env var
    is not set, i.e. when running locally outside Docker).
    """
    log_dir  = os.getenv("APP_DATA_DIR", ".")
    log_path = os.path.join(log_dir, "app.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(stream=sys.stdout),
        ],
    )
    return logging.getLogger(__name__)