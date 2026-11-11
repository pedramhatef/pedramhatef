SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    idx INTEGER PRIMARY KEY,
    block INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS challenge_meta (
    ticker TEXT PRIMARY KEY,
    dim INTEGER NOT NULL,
    blocks_ahead INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS challenge_data (
    ticker TEXT NOT NULL,
    sidx INTEGER NOT NULL,
    price REAL,
    price_data TEXT,
    hotkeys TEXT,
    embeddings BLOB,
    PRIMARY KEY (ticker, sidx)
);
CREATE TABLE IF NOT EXISTS raw_payloads (
    ts INTEGER NOT NULL,
    hotkey TEXT NOT NULL,
    payload BLOB,
    PRIMARY KEY (ts, hotkey)
);
CREATE TABLE IF NOT EXISTS drand_cache (
    round INTEGER PRIMARY KEY,
    signature BLOB
);
CREATE TABLE IF NOT EXISTS breakout_state (
    asset TEXT PRIMARY KEY,
    state_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocks_block ON blocks(block);
"""
