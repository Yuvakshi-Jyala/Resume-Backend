"""Logger setup shared by the app."""
import logging

screen_logger = logging.getLogger("screen")

cogitx_logger = logging.getLogger("cogitx")
if not cogitx_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[cogitx] %(levelname)s %(message)s"))
    cogitx_logger.addHandler(_h)
cogitx_logger.setLevel(logging.INFO)
