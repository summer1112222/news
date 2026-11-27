-- 埋点事件表(一次性手动部署:wrangler d1 execute DB --file schema.sql)
-- 字段均为非个人身份信息:vid 是前端随机 hex,无任何可识别个人的数据。
CREATE TABLE IF NOT EXISTS events (
  date TEXT NOT NULL,     -- 北京日界 YYYY-MM-DD(由 Worker 服务器按 UTC+8 写入,不信任客户端)
  vid  TEXT NOT NULL,     -- 匿名访客 ID(前端随机 hex)
  type TEXT NOT NULL,     -- view / tab / click
  cat  TEXT,              -- 分类名
  region TEXT,            -- 国内 / 国外(tab 与 click 都带)
  item INTEGER,           -- 条目序号(仅 click,0-99)
  ts   INTEGER            -- 写入时间戳毫秒
);
CREATE INDEX IF NOT EXISTS idx_date ON events(date);
