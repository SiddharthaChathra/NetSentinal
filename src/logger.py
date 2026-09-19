import logging
import os

def setup_logger():
    # Make sure logs directory exists
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "netsentinel.log")

    logger = logging.getLogger("NetSentinel")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        # Hosted platforms (Render, Docker) only capture stdout/stderr; a
        # file inside the container is invisible there. Warnings and errors
        # go to the stream too so failures show up in the platform's logs.
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.WARNING)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    
    return logger

logger = setup_logger()
