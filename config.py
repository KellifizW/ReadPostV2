GENERAL = {
    "TIMEZONE": "Asia/Hong_Kong"
}

LIHKG_API = {
    "BASE_URL": "https://api.lihkg.com/v1",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "CATEGORIES": {
        "熱門": 1,
        "最新": 2,
        "吹水": 3
    },
    "MAX_PAGES": 3,
    "MIN_REPLIES": 10,
    "CACHE_DURATION": 60,
    "RATE_LIMIT": {
        "MAX_REQUESTS": 100,
        "RESET_INTERVAL": 3600
    },
    "RATE_LIMIT_DURATION": 3600,
    "RATE_LIMIT_RESET": 3600
}

HKGOLDEN_API = {
    "BASE_URL": "https://api.hkgolden.com/v1",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "CATEGORIES": {
        "聊天": "CA",
        "時事": "NW",
        "娛樂": "ET"
    },
    "MAX_PAGES": 3,
    "MIN_REPLIES": 10,
    "CACHE_DURATION": 60,
    "RATE_LIMIT": {
        "MAX_REQUESTS": 100,
        "RESET_INTERVAL": 3600
    },
    "RATE_LIMIT_DURATION": 3600,
    "RATE_LIMIT_RESET": 3600
}
