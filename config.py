# config.py
GENERAL = {
    "TIMEZONE": "Asia/Hong_Kong"
}

LIHKG_API = {
    "BASE_URL": "https://lihkg.com",
    "CATEGORIES": {
        "最新": 1,
        "熱門": 2,
        "吹水": 11,
        "時事": 31
    },
    "MAX_PAGES": 3,
    "MIN_REPLIES": 50,
    "CACHE_DURATION": 60,
    "REQUEST_DELAY": 0.5,
    "RATE_LIMIT": {
        "MAX_REQUESTS": 30,
        "PERIOD": 60
    }
}

HKGOLDEN_API = {
    "BASE_URL": "https://api.hkgolden.com",
    "CATEGORIES": {
        "聊天": "CA",
        "時事": "NW",
        "娛樂": "ET",
        "科技": "IT"
    },
    "MAX_PAGES": 3,
    "MIN_REPLIES": 50,
    "CACHE_DURATION": 60,
    "REQUEST_DELAY": 0.5,
    "RATE_LIMIT": {
        "MAX_REQUESTS": 30,
        "PERIOD": 60
    }
}
