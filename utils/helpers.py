from datetime import datetime, timedelta
import re

def parse_time(time_str: str) -> timedelta:
    """Convert time string (e.g., '1d', '30m', '12h') to timedelta"""
    units = {
        's': 'seconds',
        'm': 'minutes',
        'h': 'hours',
        'd': 'days',
        'w': 'weeks'
    }
    
    match = re.match(r'(\d+)([smhdw])', time_str.lower())
    if not match:
        raise ValueError("Invalid time format")
    
    value, unit = match.groups()
    return timedelta(**{units[unit]: int(value)})

def format_duration(td: timedelta) -> str:
    """Format timedelta into human readable string"""
    total_seconds = int(td.total_seconds())
    
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0:
        parts.append(f"{seconds}s")
    
    return " ".join(parts)

def is_valid_duration(duration: str) -> bool:
    """Check if duration string is valid"""
    try:
        parse_time(duration)
        return True
    except ValueError:
        return False

def get_relative_time(dt: datetime) -> str:
    """Get relative time string for a datetime"""
    now = datetime.utcnow()
    diff = dt - now
    
    if diff.total_seconds() < 0:
        return "in the past"
    
    return format_duration(diff)
