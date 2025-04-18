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
    "CACHE_DURATION": 60,
    "REQUEST_DELAY": 0.5,
    "RATE_LIMIT": {
        "MAX_REQUESTS": 30,
        "PERIOD": 60
    }
}

HKGOLDEN_API = {
    "BASE_URL": "https://api.hkgolden.com",  # 修正為無 /v1
    "USER_AGENT": "Streamlit-App/1.0",
    "API_KEY": "",  # 若需認證，填入令牌
    "CATEGORIES": {
        "高登熱": "HT"
    },
    "MAX_PAGES": 3,
    "CACHE_DURATION": 60,
    "REQUEST_DELAY": 0.5,
    "RATE_LIMIT_WINDOW": 60,  # 秒，與 RATE_LIMIT["PERIOD"] 一致
    "RATE_LIMIT_REQUESTS": 30  # 與 RATE_LIMIT["MAX_REQUESTS"] 一致
}

GROK3_API = {
    "BASE_URL": "https://api.x.ai/v1",
    "API_KEY": "",  # 從 st.secrets["grok3key"] 動態載入
    "MODEL": "grok-3",
    "MAX_TOKENS": 20480,
    "RATE_LIMIT": {
        "MAX_REQUESTS": 100,
        "PERIOD": 3600
    }
}
