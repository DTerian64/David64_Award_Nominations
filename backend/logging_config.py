# logging_config.py
import logging
import sys
import json
from datetime import datetime

class JSONFormatter(logging.Formatter):
    """Format logs as JSON for better parsing in Azure"""
    
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        if hasattr(record, 'user_id'):
            log_data["user_id"] = record.user_id
        if hasattr(record, 'nomination_id'):
            log_data["nomination_id"] = record.nomination_id
        if hasattr(record, 'risk_level'):
            log_data["risk_level"] = record.risk_level
        if hasattr(record, 'fraud_score'):
            log_data["fraud_score"] = record.fraud_score
        if hasattr(record, 'warning_flags'):
            log_data["warning_flags"] = record.warning_flags
        if hasattr(record, 'beneficiary_id'):
            log_data["beneficiary_id"] = record.beneficiary_id
        
        return json.dumps(log_data)


def setup_logging():
    """Configure application logging"""
    
    # Create JSON formatter
    json_formatter = JSONFormatter()
    
    # Console handler with JSON format
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(json_formatter)
    
    # Root logger configuration
    root_logger = logging.getLogger(__name__)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    
    # Suppress noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    return root_logger