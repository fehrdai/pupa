"""
Debug logger con auto-rotation per evitare file enormi
"""
import logging
from logging.handlers import RotatingFileHandler
import os

def setup_debug_logger(name="pupa_debug", log_file="debug.log", max_bytes=100000, backup_count=3):
    """
    Setup logger che scrive su file con rotation automatica

    Args:
        name: nome logger
        log_file: file di output
        max_bytes: max dimensione file prima di rotate (100KB default)
        backup_count: quanti file backup mantenere
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Se ha già handlers, non aggiungere duplicati
    if logger.handlers:
        return logger

    # RotatingFileHandler - crea backup quando raggiunge max_bytes
    handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count
    )

    formatter = logging.Formatter(
        '[%(asctime)s] %(name)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger

# Global logger instance
debug_log = setup_debug_logger()

def debug(msg):
    """Log a debug message"""
    debug_log.debug(msg)

def info(msg):
    """Log an info message"""
    debug_log.info(msg)

def warning(msg):
    """Log a warning message"""
    debug_log.warning(msg)

def error(msg):
    """Log an error message"""
    debug_log.error(msg)
