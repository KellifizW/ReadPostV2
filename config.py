LIHKG_API = {
    "BASE_URL": "https://lihkg.com",
    "MIN_REPLIES": 125,
    "MAX_REPLIES_PER_THREAD": 3,
    "BATCH_SIZE": 3,
    "MAX_PAGES": 1,
    "CACHE_DURATION": 60,  # 秒
    "RATE_LIMIT": {
        "MAX_REQUESTS": 20,
        "PERIOD": 60
    },
    "REQUEST_DELAY": 5,
    "CATEGORIES": {
        "吹水台": 1,
        "熱門台": 2,
        "時事台": 5,
        "上班台": 14,
        "財經台": 15,
        "成人台": 29,
        "創意台": 31
    }
}

HKGOLDEN_API = {
    "BASE_URL": "https://api.hkgolden.com",
    "MIN_REPLIES": 50,
    "MAX_REPLIES_PER_THREAD": 3,
    "BATCH_SIZE": 3,
    "MAX_PAGES": 1,
    "CACHE_DURATION": 60,  # 秒
    "RATE_LIMIT": {
        "MAX_REQUESTS": 20,
        "PERIOD": 60
    },
    "REQUEST_DELAY": 5,
    "CATEGORIES": {
        "聊天": "HT"
    }
}

GROK3_API = {
    "URL": "https://api.x.ai/v1/chat/completions",
    "TOKEN_LIMIT": 8000,
    "MODEL": "grok-3-beta",
    "MAX_TOKENS": 600,
    "TEMPERATURE": 0.7,
    "RETRIES": 3,
    "TIMEOUT": 60
}

GENERAL = {
    "TIMEZONE": "Asia/Hong_Kong",
    "LOG_LEVEL": "INFO"
}