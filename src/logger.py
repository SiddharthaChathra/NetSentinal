import logging
import os
import sys


def _log_dir() -> str:
    """Where to write netsentinel.log.

    Normally alongside the source tree. When running as a PyInstaller binary
    that path is inside the temporary extraction directory, which is deleted
    the moment the process exits - so the agent writes into its own per-user
    data directory instead, where the logs survive and the user can find them.
    """
    if getattr(sys, "frozen", False):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from agent_paths import data_dir
            return str(data_dir() / "logs")
        except Exception:
            return os.path.join(os.path.expanduser("~"), ".netsentinel", "logs")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def setup_logger():
    logger = logging.getLogger("NetSentinel")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        # A log file is a convenience, never a reason to fail to start. A
        # read-only or unwritable location degrades to stream logging.
        try:
            log_dir = _log_dir()
            os.makedirs(log_dir, exist_ok=True)
            file_handler = logging.FileHandler(os.path.join(log_dir, "netsentinel.log"), encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:  # pragma: no cover - environment dependent
            print(f"NetSentinel: file logging disabled ({e})", file=sys.stderr)
        # Hosted platforms (Render, Docker) only capture stdout/stderr; a
        # file inside the container is invisible there. Warnings and errors
        # go to the stream too so failures show up in the platform's logs.
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.WARNING)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    
    return logger

logger = setup_logger()
