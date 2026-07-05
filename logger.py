import logging
import os

def setup_logger(log_file="pupa.log"):
    logger = logging.getLogger("pupa_brain")
    logger.setLevel(logging.DEBUG)
    
    os.makedirs("logs", exist_ok=True)
    fh = logging.FileHandler(f"logs/{log_file}")
    fh.setLevel(logging.DEBUG)
    
    formatter = logging.Formatter(
        '%(asctime)s - [%(levelname)s] - %(message)s',
        datefmt='%H:%M:%S'
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger

def log_decision(from_scene, to_scene, reason, energy, duration, logger, trans_type=""):
    if logger:
        trans_label = f" | {trans_type}" if trans_type else ""
        logger.info(
            f"SWITCH: {from_scene:20} -> {to_scene:20} | "
            f"{reason:50} | {energy:15} | {duration}ms{trans_label}"
        )