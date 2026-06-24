-- Sohu 金罗盘 - 券商研究员推荐数据
CREATE TABLE IF NOT EXISTS sohu_jlp (
    ts_code         VARCHAR(10) NOT NULL,
    stock_name      VARCHAR(50),
    industry        VARCHAR(50),
    analyst         TEXT,
    broker          VARCHAR(100),
    comment_date    DATE NOT NULL,
    comment_price   NUMERIC(10,2),
    target_price    NUMERIC(10,2),
    target_space    VARCHAR(20),
    price_20d       NUMERIC(10,2),
    change_20d      VARCHAR(20),
    profit_20d      VARCHAR(20),
    price_60d       NUMERIC(10,2),
    change_60d      VARCHAR(20),
    profit_60d      VARCHAR(20),
    recommend_reason TEXT,
    synced_at       TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (ts_code, analyst, broker, comment_date, comment_price)
);

CREATE INDEX IF NOT EXISTS idx_sohu_jlp_date ON sohu_jlp(comment_date);
CREATE INDEX IF NOT EXISTS idx_sohu_jlp_code ON sohu_jlp(ts_code);
CREATE INDEX IF NOT EXISTS idx_sohu_jlp_broker ON sohu_jlp(broker);
